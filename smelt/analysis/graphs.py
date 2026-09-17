from collections import deque
from collections.abc import Hashable, Mapping, Set


def strongly_connected_components[T: Hashable](
    adjacency: Mapping[T, Set[T]],
) -> list[set[T]]:
    """Tarjan's algorithm, iterative so deep graphs don't hit the recursion limit."""
    index_of: dict[T, int] = {}
    lowlink: dict[T, int] = {}
    on_stack: set[T] = set()
    stack: list[T] = []
    components: list[set[T]] = []
    counter = 0
    nodes = set(adjacency) | {n for targets in adjacency.values() for n in targets}

    for start in sorted(nodes, key=str):
        if start in index_of:
            continue
        work: list[tuple[T, list[T]]] = [
            (start, sorted(adjacency.get(start, ()), key=str))
        ]
        index_of[start] = lowlink[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            if children:
                child = children.pop(0)
                if child not in index_of:
                    index_of[child] = lowlink[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, sorted(adjacency.get(child, ()), key=str)))
                elif child in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component: set[T] = set()
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.add(member)
                    if member == node:
                        break
                components.append(component)
    return components


def shortest_cycle[T: Hashable](
    start: T, adjacency: Mapping[T, Set[T]], within: Set[T]
) -> list[T] | None:
    """Shortest path ``start -> ... -> start`` using only nodes in ``within``."""
    previous: dict[T, T] = {}
    queue: deque[T] = deque([start])
    visited: set[T] = set()
    while queue:
        node = queue.popleft()
        for child in sorted(adjacency.get(node, ()), key=str):
            if child not in within:
                continue
            if child == start:
                path = [node]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return [*reversed(path), start]
            if child not in visited:
                visited.add(child)
                previous[child] = node
                queue.append(child)
    return None
