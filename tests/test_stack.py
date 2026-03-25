"""Tests for Stack class."""

import pytest

from stack import Stack


class TestStack:
    def test_new_stack_is_empty(self):
        assert Stack().is_empty()
        assert Stack().size() == 0

    def test_push_makes_non_empty(self):
        s = Stack()
        s.push(1)
        assert not s.is_empty()
        assert s.size() == 1

    def test_push_pop_lifo(self):
        s = Stack()
        s.push("a")
        s.push("b")
        assert s.pop() == "b"
        assert s.pop() == "a"

    def test_peek_returns_top_without_removing(self):
        s = Stack()
        s.push(42)
        assert s.peek() == 42
        assert s.size() == 1

    def test_pop_empty_raises(self):
        with pytest.raises(IndexError, match="pop from empty stack"):
            Stack().pop()

    def test_peek_empty_raises(self):
        with pytest.raises(IndexError, match="peek at empty stack"):
            Stack().peek()

    def test_multiple_push_pop_cycles(self):
        s = Stack()
        s.push(1)
        s.push(2)
        assert s.pop() == 2
        s.push(3)
        assert s.pop() == 3
        assert s.pop() == 1
        assert s.is_empty()

    def test_iter_top_to_bottom(self):
        s = Stack()
        for v in [1, 2, 3]:
            s.push(v)
        assert list(s) == [3, 2, 1]

    def test_repr(self):
        s = Stack()
        s.push(1)
        s.push(2)
        assert repr(s) == "Stack([2, 1])"

    def test_size_tracks_correctly(self):
        s = Stack()
        for i in range(5):
            s.push(i)
        assert s.size() == 5
        s.pop()
        assert s.size() == 4
