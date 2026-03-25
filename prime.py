"""Prime number checker."""

import sys
from math import isqrt


def is_prime(n: int) -> bool:
    """Return True if n is prime, False otherwise."""
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    for i in range(5, isqrt(n) + 1, 6):
        if n % i == 0 or n % (i + 2) == 0:
            return False
    return True


if __name__ == "__main__":
    num = int(sys.argv[1])
    print(f"{num} is {'prime' if is_prime(num) else 'not prime'}")
