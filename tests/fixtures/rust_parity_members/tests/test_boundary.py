from app.modular.record import Record


def test_size():
    record = Record()
    assert record.size == 1
