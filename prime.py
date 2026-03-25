def is_prime(n: int) -> bool:
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"Expected int, got {type(n).__name__}")
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
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"Expected int, got {type(n).__name__}")
    if n < 2:
        return []
    sieve = [True] * (n + 1)
    sieve[0] = sieve[1] = False
    for i in range(2, int(n**0.5) + 1):
        if sieve[i]:
            for j in range(i * i, n + 1, i):
                sieve[j] = False
    return [i for i, flag in enumerate(sieve) if flag]


def nth_prime(n: int) -> int:
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"Expected int, got {type(n).__name__}")
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    count, candidate = 0, 1
    while count < n:
        candidate += 1
        if is_prime(candidate):
            count += 1
    return candidate


if __name__ == "__main__":
    print(f"Primes up to 50: {primes_up_to(50)}")
