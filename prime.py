from math import isqrt


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    return all(n % i != 0 for i in range(3, isqrt(n) + 1, 2))


if __name__ == "__main__":
    primes = [n for n in range(51) if is_prime(n)]
    print(f"Primes up to 50: {primes}")
