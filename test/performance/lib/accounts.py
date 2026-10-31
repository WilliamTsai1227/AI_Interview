"""把測試帳號分給 Locust 使用者：每個使用者一個帳號，用完從頭再來。

同一個帳號同時只能有一場進行中的面試，所以帳號數（PERF_ACCOUNTS）要 ≥ 同時在線的使用者數。
"""
import itertools

from lib import config

_next = itertools.count()


def take() -> str:
    return config.email(next(_next) % config.ACCOUNTS)
