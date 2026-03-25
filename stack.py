"""A simple stack data structure implementation."""


class Stack:
    """A LIFO stack backed by a Python list."""

    def __init__(self):
        """Initialize an empty stack."""
        self._items: list = []

    def push(self, item) -> None:
        """Add an item to the top of the stack."""
        self._items.append(item)

    def pop(self):
        """Remove and return the top item. Raises IndexError if empty."""
        if self.is_empty():
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self):
        """Return the top item without removing it. Raises IndexError if empty."""
        if self.is_empty():
            raise IndexError("peek at empty stack")
        return self._items[-1]

    def is_empty(self) -> bool:
        """Return True if the stack has no items."""
        return len(self._items) == 0

    def size(self) -> int:
        """Return the number of items in the stack."""
        return len(self._items)

    def __len__(self) -> int:
        """Support len() built-in."""
        return self.size()


if __name__ == "__main__":
    s = Stack()
    print(f"Empty? {s.is_empty()}")  # True

    for val in [10, 20, 30]:
        s.push(val)
        print(f"Pushed {val}, size={len(s)}")

    print(f"Peek: {s.peek()}")   # 30
    print(f"Size: {s.size()}")  # 3
    print(f"Pop:  {s.pop()}")   # 30
    print(f"Pop:  {s.pop()}")   # 20
    print(f"Size: {s.size()}")  # 1
    print(f"Empty? {s.is_empty()}")  # False
