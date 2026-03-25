"""Fibonacci number generators — iterative and recursive."""


def fib(n: int) -> int:
    """Return the nth Fibonacci number using iteration.

    Args:
        n: Non-negative index into the Fibonacci sequence (0-indexed).

    Returns:
        The nth Fibonacci number.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def fib_recursive(n: int) -> int:
    """Return the nth Fibonacci number using recursion.

    Args:
        n: Non-negative index into the Fibonacci sequence (0-indexed).

    Returns:
        The nth Fibonacci number.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if n < 2:
        return n
    return fib_recursive(n - 1) + fib_recursive(n - 2)


if __name__ == "__main__":
    for i in range(10):
        print(f"fib({i}) = {fib(i)}")
