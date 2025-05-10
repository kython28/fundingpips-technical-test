import struct, random, sys

seed = int(sys.argv[1])
hours = int(sys.argv[2])

random.seed(seed)

fmt = "=QQQQBQIQQ"
account_ids = [random.randint(0, 100) for _ in range(random.randint(10, 1000))]
user_ids = [
    random.randint(0, 100) for _ in range(random.randint(1, min(100, len(account_ids))))
]

print(user_ids)

start_ts = 0
current_ts = start_ts
trade_id = 0
with open("dataset.bin", "wb") as file:
    while (current_ts - start_ts) < (hours * 60 * 60 * 1000):
        end_ts = current_ts + random.randint(100, 60 * 60 * 1000)
        duration = end_ts - current_ts

        lot_size = random.randint(100_000, 100 * 10**8)
        side = random.randint(0, 1)
        symbol = random.randint(0, 4)

        account_id = random.choice(account_ids)
        user_id = random.choice(user_ids)

        current_ts += random.randint(10, 20_000)

        file.write(
            struct.pack(
                fmt,
                current_ts,
                end_ts,
                duration,
                lot_size,
                side,
                trade_id,
                symbol,
                account_id,
                user_id,
            )
        )
        if trade_id % 1_000_000 == 0:
            print(trade_id)
        trade_id += 1

print(trade_id)
