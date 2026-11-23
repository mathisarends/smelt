import os
import random
import secrets
import uuid
from random import randint as pick
from uuid import uuid4

import pytest


def test_generators():
    assert uuid.uuid1() and uuid4()
    assert os.urandom(4)
    assert secrets.choice([1]) and secrets.randbelow(2) and secrets.randbits(2)
    assert secrets.token_bytes() and secrets.token_hex() and secrets.token_urlsafe()
    assert random.random() and pick(1, 2) and random.randrange(2)
    assert random.choice([1]) and random.choices([1]) and random.sample([1], 1)
    random.shuffle([1])
    assert random.uniform(1, 2) and random.getrandbits(2) and random.randbytes(2)


def test_seeded():
    generator = random.Random(1)
    assert generator.random()


def test_repeated_on_one_line():
    assert random.random() and random.random()


@pytest.fixture
def identifier():
    return uuid.uuid4()


VALUE = random.random()


def helper():
    return uuid4()
