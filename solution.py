from pydantic import BaseModel, ValidationError

from typing import Self, Literal, Iterator, TypeVar, Any
from abc import abstractmethod
from time import perf_counter
import struct, sys, os, json

config_file = sys.argv[1]
user_id1 = int(sys.argv[2])
user_id2 = int(sys.argv[3])


Sides = ("Short", "Long")

class Config(BaseModel):
    dataset_path: str
    symbols_path: str
    mode: Literal["A", "B"]


with open(config_file) as f:
    try:
        config = Config.model_validate_json(f.read())
    except ValidationError as e:
        print("An error occurred while parsing the config file:")
        print(e)
        sys.exit(1)

with open(config.symbols_path) as f:
    Symbols = json.load(f)


USING_MODE_B = config.mode == "B"

# ------------------------------------------------------------------------------
# Dataset structure:
# - Open date (millisecods timestamp)
# - Close date (millisecods timestamp)
# - Duration (integer. in milliseconds)
# - lot size (integer. L * 10 ^ 8)
# - side (enum(int or boolean) : 1 = buy, 0 = sell)
# - trade ID (integer)
# - Symbol (enum(int))
# - Account ID (integer)
# - User ID (integer)
# ------------------------------------------------------------------------------
OPEN_DATE_INDEX = 0
CLOSE_DATE_INDEX = 1
DURATION_INDEX = 2
LOT_SIZE_INDEX = 3
SIDE_INDEX = 4
TRADE_ID_INDEX = 5
SYMBOL_INDEX = 6
ACCOUNT_ID_INDEX = 7
USER_ID_INDEX = 8
Trade = tuple[int, int, int, int, int, int, int, int, int]


def read_trades() -> Iterator[Trade]:
    with open(config.dataset_path, "rb") as file:
        size = file.seek(0, os.SEEK_END)
        file.seek(0)

        fmt = "=QQQQBQIQQ"
        block_size = struct.calcsize(fmt)

        if size % block_size != 0:
            raise ValueError("Dataset size is not a multiple of block size")

        while chunk := file.read(block_size * 1000):
            for trade in struct.iter_unpack(fmt, chunk):
                yield trade


MINIMUM_TRADE_DURATION = 1000
MATCHING_TIME_WINDOW = 60 * 5 * 1000
MINIMUM_LOT_SIZE = 10**6  # ~ 0.01 * 10 ** 8


class TradesBatch:
    # We use __slots__ to optimize memory usage and improve performance
    # By defining __slots__, we:
    # 1. Prevent the creation of a __dict__ for each instance
    # 2. Reduce memory overhead for each TradesBatch object
    # 3. Slightly speed up attribute access
    # 4. Explicitly define the allowed attributes for the class
    __slots__ = (
        "parent_trade",
        "next_batch",
        "similar_trades",
    )

    def __init__(self, trade: Trade, next_batch: Self | None) -> None:
        self.parent_trade: Trade = trade
        self.next_batch: Self | None = next_batch

        self.similar_trades: list[Trade] = []

    def _validate(
        self,
        new_trade_open_ts: int,
        new_trade_close_ts: int,
        new_trade_account_id: int,
        parent_trade_open_ts: int,
        parent_trade_close_ts: int,
        parent_account_id: int,
    ) -> int:
        # This method checks if a new trade is within the matching time window of the parent trade.
        # If the new trade's open timestamp is more than MATCHING_TIME_WINDOW (5 minutes) after the parent trade's
        # open timestamp, it returns 0, indicating the trade has expired and cannot be matched.
        if (new_trade_open_ts - parent_trade_open_ts) > MATCHING_TIME_WINDOW:
            return 0  # Expired

        if new_trade_account_id == parent_account_id:
            return 1  # Same account

        if (new_trade_close_ts - parent_trade_close_ts) > MATCHING_TIME_WINDOW:
            return 2  # Close time not matching

        return 3

    @abstractmethod
    def submit_trade(
        self,
        new_trade: Trade,
        new_trade_open_ts: int,
        new_trade_close_ts: int,
        new_trade_account_id: int,
        value_to_validate: Any,
    ) -> int:
        raise NotImplementedError


class CopyTradesBatch(TradesBatch):
    def submit_trade(
        self,
        new_trade: Trade,
        new_trade_open_ts: int,
        new_trade_close_ts: int,
        new_trade_account_id: int,
        value_to_validate: Any,
    ) -> int:
        parent_trade = self.parent_trade

        parent_open_ts = parent_trade[OPEN_DATE_INDEX]
        parent_close_ts = parent_trade[CLOSE_DATE_INDEX]
        parent_side = parent_trade[SIDE_INDEX]
        parent_account_id = parent_trade[ACCOUNT_ID_INDEX]

        ret = self._validate(
            new_trade_open_ts,
            new_trade_close_ts,
            new_trade_account_id,
            parent_open_ts,
            parent_close_ts,
            parent_account_id,
        )

        # This method determines if a trade is a valid copy trade
        # It checks various conditions to validate if the new trade is a copy of the parent trade
        same_side = parent_side == value_to_validate
        match ret:
            case 1:
                # If the accounts are the same, but the trade sides are the same,
                # return 3 to indicate the trade cannot be added
                if same_side:
                    return 3
            case 3:
                # If previous validation returned 3, continue processing
                pass
            case _:
                # For any other validation result, return the result
                return ret

        if not same_side:
            # A copy trade must have the same trading side as the parent trade
            return 3

        # If all conditions are met, add the trade to similar trades
        self.similar_trades.append(new_trade)
        return 4


class ReversalTradesBatch(TradesBatch):
    def submit_trade(
        self,
        new_trade: Trade,
        new_trade_open_ts: int,
        new_trade_close_ts: int,
        new_trade_account_id: int,
        value_to_validate: Any,
    ) -> int:
        parent_trade = self.parent_trade

        parent_open_ts = parent_trade[OPEN_DATE_INDEX]
        parent_close_ts = parent_trade[CLOSE_DATE_INDEX]
        parent_account_id = parent_trade[ACCOUNT_ID_INDEX]

        ret = self._validate(
            new_trade_open_ts,
            new_trade_close_ts,
            new_trade_account_id,
            parent_open_ts,
            parent_close_ts,
            parent_account_id,
        )

        # This method determines if a trade is a valid reversal trade
        # First, if the validation result is less than 3, return the validation result
        if ret < 3:
            return ret

        # A reversal trade is only valid if the trade side is different from the parent trade
        # If the sides are the same, return 3 to indicate the trade cannot be added
        if parent_trade[SIDE_INDEX] == value_to_validate:
            return 3

        # If all conditions are met, add the trade to similar trades
        self.similar_trades.append(new_trade)
        return 4


class PartialCopyTradesBatch(TradesBatch):
    def submit_trade(
        self,
        new_trade: Trade,
        new_trade_open_ts: int,
        new_trade_close_ts: int,
        new_trade_account_id: int,
        value_to_validate: Any,
    ) -> int:
        parent_trade = self.parent_trade

        parent_open_ts = parent_trade[OPEN_DATE_INDEX]
        parent_close_ts = parent_trade[CLOSE_DATE_INDEX]
        parent_account_id = parent_trade[ACCOUNT_ID_INDEX]

        ret = self._validate(
            new_trade_open_ts,
            new_trade_close_ts,
            new_trade_account_id,
            parent_open_ts,
            parent_close_ts,
            parent_account_id,
        )

        # This method determines if a trade is a valid partial copy trade
        # 1. If the validation result is less than 3, return the validation result
        if ret < 3:
            return ret

        # 2. Check if the lot size is within 30% of the parent trade's lot size
        # A relative difference greater than 30% means the trade is not considered a partial copy
        relative_diff = value_to_validate / parent_trade[LOT_SIZE_INDEX] - 1
        if abs(relative_diff) > 0.3:  # Note: This is a simplified comparison method
            return 3

        # 3. If all conditions are met, add the trade to similar trades
        self.similar_trades.append(new_trade)
        return 4


T = TypeVar("T", bound=TradesBatch)


def categorize_trade(
    trade: Trade, batch: T | None, constructor: type[T], queue: list[T], index: int
) -> T:
    open_ts = trade[OPEN_DATE_INDEX]
    close_ts = trade[CLOSE_DATE_INDEX]
    value_to_validate = trade[index]
    account_id = trade[ACCOUNT_ID_INDEX]
    first_batch = batch
    prev_batch: T | None = None

    while batch:
        match batch.submit_trade(
            trade, open_ts, close_ts, account_id, value_to_validate
        ):
            case 0:  # Expired
                # When a batch expires (time window exceeded):
                # 1. If the batch has similar trades, add it to the queue
                # 2. Update the first batch reference if needed
                # 3. Remove the expired batch from the linked list
                # 4. Move to the next batch
                if batch.similar_trades:
                    queue.append(batch)

                next_batch = batch.next_batch
                if batch == first_batch:
                    first_batch = next_batch
                else:
                    prev_batch.next_batch = next_batch  # type: ignore

                batch = next_batch
            case 1:  # Same account
                # When a batch has the same account as the new trade:
                # 1. If the current batch has similar trades, add it to the queue
                # 2. Create a new batch with the current trade
                # 3. Update the first batch reference if needed
                # 4. Remove the current batch from the linked list
                # 5. Return the first batch of the linked list
                if batch.similar_trades:
                    queue.append(batch)

                new_batch = constructor(trade, batch.next_batch)
                if batch == first_batch:
                    first_batch = new_batch
                else:
                    prev_batch.next_batch = new_batch  # type: ignore

                # When a batch is successfully added to the results:
                # 1. Return the first batch of the linked list
                # 2. The type: ignore comment suppresses type checking warnings
                # This ensures the first batch is updated and returned correctly
                return first_batch  # type: ignore

            case (
                2 | 3
            ):  # Close time not matching, evaluation failed
                # When a batch cannot be added to the results (close time not matching),
                # we will move to the next batch in the linked list
                prev_batch = batch
                batch = batch.next_batch
            case 4:  # success
                # When a batch is successfully added to the results:
                # Return the first batch of the linked list
                return first_batch  # type: ignore

    # When no existing batch matches the trade, we:
    # 1. Create a new batch with the current trade as the parent trade
    # 2. Set the first batch of the linked list as the next batch
    # 3. Return the newly created batch as the first batch
    new_batch = constructor(trade, first_batch)
    return new_batch


def add_remaining_batches(
    batch_per_symbol: dict[int, T | None], queues_per_symbol: dict[int, list[T]]
) -> None:
    for symbol, batch in batch_per_symbol.items():
        while batch:
            if batch.similar_trades:
                queues_per_symbol[symbol].append(batch)
            batch = batch.next_batch


def save_report(
    filename: str, data: dict[int, list[T]], report_violation: bool
) -> tuple[int, int]:
    tittles = (
        "Trade ID A",
        "Trade ID B",
        "User ID A",
        "User ID B",
        "Account ID A",
        "Account ID B",
        "Symbol",
        "Side A",
        "Side B",
        "Lot size A",
        "Lot size B",
        "Trade open date A",
        "Trade close date A",
        "Trade open date B",
        "Trade close date B",
    )

    violations_count = 0
    total_count = 0

    with open(f"results/{filename}.csv", "w") as file:
        file.write(",".join(tittles))
        if report_violation:
            file.write(",Violation")
        file.write("\n")

        for symbol, batches in data.items():
            for batch in batches:
                parent_trade = batch.parent_trade
                parent_trade_id = parent_trade[TRADE_ID_INDEX]
                parent_user_id = parent_trade[USER_ID_INDEX]
                parent_account_id = parent_trade[ACCOUNT_ID_INDEX]
                parent_side = parent_trade[SIDE_INDEX]
                parent_lot_size = parent_trade[LOT_SIZE_INDEX]
                parent_open_ts = parent_trade[OPEN_DATE_INDEX]
                parent_close_ts = parent_trade[CLOSE_DATE_INDEX]

                similar_trades = batch.similar_trades
                total_count += 1 + len(similar_trades)

                for trade in similar_trades:
                    user_id = trade[USER_ID_INDEX]
                    file.write(
                        ",".join(
                            str(x)
                            for x in (
                                parent_trade_id,
                                trade[TRADE_ID_INDEX],
                                parent_user_id,
                                user_id,
                                parent_account_id,
                                trade[ACCOUNT_ID_INDEX],
                                Symbols[symbol],
                                Sides[parent_side],
                                Sides[trade[SIDE_INDEX]],
                                parent_lot_size,
                                trade[LOT_SIZE_INDEX],
                                parent_open_ts,
                                trade[OPEN_DATE_INDEX],
                                parent_close_ts,
                                trade[CLOSE_DATE_INDEX],
                            )
                        )
                    )

                    if report_violation:
                        violation = user_id == parent_user_id
                        if violation:
                            violations_count += 1

                        file.write("," + ("No", "Yes")[violation])

                    file.write("\n")

    return total_count, violations_count


copy_trades: dict[int, list[CopyTradesBatch]] = {x: [] for x in range(len(Symbols))}
reversal_trades: dict[int, list[ReversalTradesBatch]] = {
    x: [] for x in range(len(Symbols))
}
partial_copy_trades: dict[int, list[PartialCopyTradesBatch]] = {
    x: [] for x in range(len(Symbols))
}

copy_trades_batch: dict[int, CopyTradesBatch | None] = dict.fromkeys(
    range(len(Symbols)), None
)
reversal_trades_batch: dict[int, ReversalTradesBatch | None] = dict.fromkeys(
    range(len(Symbols)), None
)
partial_copy_trades_batch: dict[int, PartialCopyTradesBatch | None] = dict.fromkeys(
    range(len(Symbols)), None
)


t0 = perf_counter()
for trade in read_trades():
    # ---------------
    # First filter applied to all trades:
    # 1. Check if the trade belongs to the specified user IDs
    # 2. Filter out trades that are too short in duration and have a small lot size
    user_id = trade[USER_ID_INDEX]
    if user_id not in (user_id1, user_id2):
        continue

    if (
        trade[DURATION_INDEX] <= MINIMUM_TRADE_DURATION
        and trade[LOT_SIZE_INDEX] < MINIMUM_LOT_SIZE
    ):
        continue
    # ---------------

    symbol = trade[SYMBOL_INDEX]

    # ---------------
    # Second filter for trades that pass the initial criteria:
    # Categorize trades into different types:
    # 1. Copy trades (using side index 4)
    # 2. Reversal trades (using side index 4)
    # 3. Partial copy trades (using side index 3)
    # For each trade type, update the corresponding batch
    copy_trades_batch[symbol] = categorize_trade(
        trade, copy_trades_batch[symbol], CopyTradesBatch, copy_trades[symbol], 4
    )
    reversal_trades_batch[symbol] = categorize_trade(
        trade,
        reversal_trades_batch[symbol],
        ReversalTradesBatch,
        reversal_trades[symbol],
        4,
    )
    partial_copy_trades_batch[symbol] = categorize_trade(
        trade,
        partial_copy_trades_batch[symbol],
        PartialCopyTradesBatch,
        partial_copy_trades[symbol],
        3,
    )
    # ---------------

# --------------------
# Add any remaining batches to their respective result lists
# This ensures that any batches not processed during the main loop are still included in the final results
add_remaining_batches(copy_trades_batch, copy_trades)
add_remaining_batches(reversal_trades_batch, reversal_trades)
add_remaining_batches(partial_copy_trades_batch, partial_copy_trades)
# --------------------

categorizing_time = perf_counter() - t0
print("Trade comparison completed. ({:.3f}s)".format(categorizing_time))
print(f"Accounts analyzed: {user_id1} vs {user_id2}")

copy_trades_matches, violations = save_report("copy_trades", copy_trades, USING_MODE_B)
reversal_trades_matches, violations = save_report(
    "reversal_trades", reversal_trades, USING_MODE_B
)
partial_copy_trades_matches, violations = save_report(
    "partial_copy_trades", partial_copy_trades, USING_MODE_B
)

total_matches = (
    copy_trades_matches + reversal_trades_matches + partial_copy_trades_matches
)
print(f"Total matches {total_matches}")
print(f" - Copy trades: {copy_trades_matches}")
print(f" - Reversal trades: {reversal_trades_matches}")
print(f" - Partial copy trades: {partial_copy_trades_matches}")
if USING_MODE_B:
    print(f" - Violations: {violations}")
