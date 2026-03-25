def fibonacci(n: int) -> int:
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


if __name__ == "__main__":
    for i in range(10):
        print(f"fibonacci({i}) = {fibonacci(i)}")
