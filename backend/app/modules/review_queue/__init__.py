"""The review queue: everything that waits for a person to look at it, in one list.

This module owns no tables. It reads each owning module's own read functions, and the work
itself is still done on the owning module's screen and routes.
"""
