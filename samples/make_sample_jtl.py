import csv
import random
import time

random.seed(7)
start = int(time.time() * 1000) - 120_000
base_ms = {"GET /api/users": 120, "GET /api/users/{id}": 150,
           "POST /api/login": 300, "POST /api/orders": 450}

with open("samples/sample-results.jtl", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timeStamp", "elapsed", "label", "responseCode", "responseMessage", "success"])
    for sec in range(120):
        degradation = 1 + sec / 60            # latency roughly triples over the run
        error_rate = 0.002 if sec < 80 else 0.06
        for _ in range(25):                   # about 25 requests per second
            label = random.choice(list(base_ms))
            elapsed = int(random.lognormvariate(0, 0.35) * base_ms[label] * degradation)
            failed = random.random() < error_rate
            w.writerow([start + sec * 1000 + random.randint(0, 999), elapsed, label,
                        500 if failed else 200, "Error" if failed else "OK",
                        "false" if failed else "true"])
print("wrote samples/sample-results.jtl")