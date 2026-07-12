"""Small shared helpers with no natural home in a specific package."""


def lazy_singleton(factory):
    """Wrap a zero-arg constructor into a cached accessor.

    Replaces the repeated `_x = None` + `global _x` + `if _x is None` idiom
    that every collector/detector/db module used to hand-roll for its own
    lazily-constructed singleton instance.
    """
    instance = None

    def get():
        nonlocal instance
        if instance is None:
            instance = factory()
        return instance

    return get
