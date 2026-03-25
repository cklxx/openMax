"""Prime number utilities."""


def is_prime(n: int) -> bool:
    """Return True if n is a prime number."""
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


def primes_up_to(n: int) -> list[int]:
    """Return all prime numbers up to n (inclusive)."""
    return [x for x in range(2, n + 1) if is_prime(x)]


if __name__ == "__main__":
    print(primes_up_to(50))
