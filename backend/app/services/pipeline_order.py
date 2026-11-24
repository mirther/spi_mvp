from collections import deque
from collections.abc import Iterable, Sequence


def get_execution_order(
    node_ids: Sequence[str],
    connections: Iterable[tuple[str, str]],
) -> list[str]:
    """Return a stable topological execution order for a project's module graph.

    The initial queue follows node insertion order. Newly-ready nodes are appended
    in connection order. This matches the frontend's getOperationOrder() helper.
    Run All should use this result to order its worker steps.
    """
    ordered_node_ids = list(node_ids)
    indegree = {node_id: 0 for node_id in ordered_node_ids}
    outgoing = {node_id: [] for node_id in ordered_node_ids}

    for source_id, target_id in connections:
        if source_id not in indegree or target_id not in indegree:
            raise ValueError("A pipeline connection references an unknown module.")
        outgoing[source_id].append(target_id)
        indegree[target_id] += 1

    ready = deque(node_id for node_id in ordered_node_ids if indegree[node_id] == 0)
    result: list[str] = []

    while ready:
        current = ready.popleft()
        result.append(current)

        for target_id in outgoing[current]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                ready.append(target_id)

    if len(result) != len(ordered_node_ids):
        raise ValueError("Pipeline connections contain a cycle.")

    return result
