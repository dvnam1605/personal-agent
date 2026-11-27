"""Chat commands of the query orchestrator: memory, web, routines, analysis, minutes,
goals and to-do tasks.

Each module contributes one mixin to the orchestrator chain (see ``query/base.py``).
Nothing is re-exported here, so importing one command module never drags in the rest
of the chain out of order.
"""
