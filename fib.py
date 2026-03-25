def fib(n: int) -> int:
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def fib_sequence(n: int) -> list[int]:
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return [fib(i) for i in range(n)]


if __name__ == "__main__":
    print(fib_sequence(10))
