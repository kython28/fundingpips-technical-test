from datetime import datetime
import sys, struct, json


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

output_file = sys.argv[1]
acounts_file = sys.argv[2]
filenames = sys.argv[3:]

symbols: list[str] = []
user_id_per_account_id: dict[int, int] = {}

trades: list[tuple[int, int, int, int, int, int, int, int, int]] = []

with open(acounts_file, "r") as file:
    file.readline() # Skip header
    for line in file:
        values = line.strip().split(",")

        account_id = int(values[0])
        user_id = int(values[-2])

        user_id_per_account_id[account_id] = user_id


for filename in filenames:
    with  open(filename, "r") as file:
        file.readline() # Skip header
        # ,identifier,action,reason,open_price,close_price,commission,lot_size,opened_at,closed_at,pips,price_sl,
        # price_tp,profit,swap,symbol,contract_size,profit_rate,platform,trading_account_login
        for line in file:
            values = line.strip().split(",")

            trade_id = int(values[1])
            action = int(values[2])
            lot_size = int(round(float(values[7]) * 10**8))
            open_ts = int( datetime.strptime(values[8], "%Y-%m-%d %H:%M:%S.%f").timestamp() * 1000 )
            close_ts = int( datetime.strptime(values[9], "%Y-%m-%d %H:%M:%S.%f").timestamp() * 1000 )
            symbol = values[15]
            account_id = int(values[-1])

            if symbol not in symbols:
                symbols.append(symbol)
                symbol_index = len(symbols) - 1
            else:
                symbol_index = symbols.index(symbol)

            trades.append((
                open_ts,
                close_ts,
                (close_ts - open_ts),
                lot_size,
                action,
                trade_id,
                symbol_index,
                account_id,
                user_id_per_account_id[account_id],
            ))

trades.sort()

with open(output_file, "wb") as file:
    for trade in trades:
        file.write(struct.pack("=QQQQBQIQQ", *trade))

with open("symbols.json", "w") as file:
    json.dump(symbols, file)
