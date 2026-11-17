from api.features.orders.application.service import serve


def test_service():
    assert serve() == 1
