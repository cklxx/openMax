"""Simple stack data structure."""


class Stack:
    def __init__(self):
        self._items: list = []

    def push(self, item) -> None:
        self._items.append(item)

    def pop(self):
        if self.is_empty():
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self):
        if self.is_empty():
            raise IndexError("peek at empty stack")
        return self._items[-1]

    def is_empty(self) -> bool:
        return len(self._items) == 0

    def size(self) -> int:
        return len(self._items)

    def __len__(self) -> int:
        return self.size()

    def __iter__(self):
        return reversed(self._items)

    def __repr__(self) -> str:
        return f"Stack({list(reversed(self._items))})"


if __name__ == "__main__":
    s = Stack()
    for val in [1, 2, 3]:
        s.push(val)
    print(f"Stack: {s}")
    print(f"Size: {s.size()}")
    print(f"Peek: {s.peek()}")
    print(f"Pop: {s.pop()}")
    print(f"After pop: {s}")
    print(f"Iteration: {list(s)}")
