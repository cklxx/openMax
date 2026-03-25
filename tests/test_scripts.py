"""Tests for fib.py, prime.py, and stack.py."""

import pytest

from fib import fibonacci
from prime import is_prime
from stack import Stack


class TestFibonacci:
    def test_base_cases(self):
        assert fibonacci(0) == 0
        assert fibonacci(1) == 1

    def test_known_values(self):
        assert fibonacci(2) == 1
        assert fibonacci(5) == 5
        assert fibonacci(10) == 55

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            fibonacci(-1)


class TestIsPrime:
    def test_small_non_primes(self):
        for n in (-1, 0, 1):
            assert is_prime(n) is False

    def test_small_primes(self):
        for n in (2, 3, 5, 7, 11, 13):
            assert is_prime(n) is True

    def test_composite(self):
        assert is_prime(4) is False
        assert is_prime(9) is False
        assert is_prime(25) is False

    def test_large_prime(self):
        assert is_prime(104729) is True


class TestStack:
    def test_push_pop(self):
        s = Stack()
        s.push(1)
        s.push(2)
        assert s.pop() == 2
        assert s.pop() == 1

    def test_peek(self):
        s = Stack()
        s.push(42)
        assert s.peek() == 42
        assert s.size() == 1

    def test_empty_pop_raises(self):
        with pytest.raises(IndexError):
            Stack().pop()

    def test_empty_peek_raises(self):
        with pytest.raises(IndexError):
            Stack().peek()

    def test_is_empty(self):
        s = Stack()
        assert s.is_empty() is True
        s.push(1)
        assert s.is_empty() is False

    def test_len(self):
        s = Stack()
        assert len(s) == 0
        s.push("a")
        s.push("b")
        assert len(s) == 2

    def test_repr(self):
        s = Stack()
        s.push(1)
        s.push(2)
        assert "Stack(" in repr(s)
