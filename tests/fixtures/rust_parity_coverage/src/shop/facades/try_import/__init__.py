try:
    import fast
except ImportError:
    import slow as fast
except (errors.Missing):
    pass
else:
    pass
finally:
    pass
