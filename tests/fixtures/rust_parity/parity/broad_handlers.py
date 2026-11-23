"""Broad handlers with and without a stated reason."""

import builtins


def run(work):
    try:
        work()
    except:
        pass
    try:
        work()
    except Exception:
        pass
    try:
        work()
    except BaseException:
        pass
    try:
        work()
    except builtins.Exception:
        pass
    try:
        work()
    except (ValueError, Exception, BaseException):
        pass
    try:
        work()
    except ValueError:
        pass
    try:
        work()
    except other.Exception:
        pass
    try:
        work()
    except* Exception:
        pass


def reasons(work):
    try:
        work()
    except Exception:  # plugins may fail in any way
        pass
    try:
        work()
    # a reason directly above
    # spanning two lines
    except Exception:
        pass
    try:
        work()
    # a reason with a gap

    except Exception:
        pass
    try:
        work()
    except Exception:
        # a reason in the body
        pass
    try:
        work()
    except Exception:
        pass  # trailing on the first statement
    try:
        work()
    except Exception:
        pass
        # too late
    try:
        work()  # trailing on the try body
    except Exception:
        pass
    try:
        work()
    except (
        Exception  # inside the header
    ):
        pass
    try:
        work()
    except Exception:

        # after a blank line

        pass


def directives(work):
    try:
        work()
    except Exception:  # noqa
        pass
    try:
        work()
    except Exception:  # noqa: BLE001
        pass
    try:
        work()
    except Exception:  # noqa: BLE001, S110 E722
        pass
    try:
        work()
    except Exception:  # NOQA: ble001
        pass
    try:
        work()
    except Exception:  # noqa: BLE001 - boundary of the plugin host
        pass
    try:
        work()
    except Exception:  # noqa BLE001
        pass
    try:
        work()
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    try:
        work()
    except Exception:  # pylint: disable-next=a-b, c_d
        pass
    try:
        work()
    except Exception:  # pylint: disable=broad-exception-caught because plugins
        pass
    try:
        work()
    except Exception:  # pylint: disable=foóbar
        pass
    try:
        work()
    except Exception:  # pylint: disable=foóbar
        pass
    try:
        work()
    except Exception:  # type: ignore
        pass
    try:
        work()
    except Exception:  # type: ignore[misc]
        pass
    try:
        work()
    except Exception:  # pyright: ignore[reportGeneralTypeIssues]
        pass
    try:
        work()
    except Exception:  # pragma: no cover
        pass
    try:
        work()
    except Exception:  # pragma: no   branch
        pass
    try:
        work()
    except Exception:  # pragma: no cover -- only in production
        pass
    try:
        work()
    except Exception:  # smelt: ignore[CPX003]
        pass
    try:
        work()
    except Exception:  # smelt: ignore-file[CPX003]
        pass
    try:
        work()
    except Exception:  # fmt: skip
        pass
    try:
        work()
    except Exception:  # fmt: off
        pass
    try:
        work()
    except Exception:  # noqa # type: ignore # pragma: no cover
        pass
    try:
        work()
    except Exception:  # noqa # the real reason
        pass
    try:
        work()
    except Exception:  # noqa type: ignore fmt: skip
        pass
    try:
        work()
    except Exception:  # noqa -; type: ignore
        pass


def unusual_comments(work):
    try:
        work()
    except Exception:  #
        pass
    try:
        work()
    except Exception:  # ---
        pass
    try:
        work()
    except Exception:  # !?
        pass
    try:
        work()
    except Exception:  # 7
        pass
    try:
        work()
    except Exception:  # ²
        pass
    try:
        work()
    except Exception:  # Ⅷ
        pass
    try:
        work()
    except Exception:  # ́
        pass
    try:
        work()
    except Exception:  # 日本語
        pass
    try:
        work()
    except Exception:  # 🙂
        pass
    try:
        work()
    except Exception:  # ſmelt: ignore
        pass
    try:
        work()
    except Exception:  # type: ıgnore
        pass
    try:
        work()
    except Exception:  # type: İgnore
        pass
    try:
        work()
    except Exception:  # fmt: sKip
        pass
    try:
        work()
    except Exception:  # noqa: ıſK1
        pass
    try:
        work()
    except Exception:  #  noqa:BLE001
        pass
    try:
        work()
    except Exception:  # noqa:BLE001
        pass
    try:
        work()
    except Exception:  # noqa: BLE
        pass
    try:
        work()
    except Exception:  # type:ignore[
        pass


def propagating(work):
    try:
        work()
    except Exception:
        raise
    try:
        work()
    except Exception as error:
        raise RuntimeError from error
    try:
        work()
    except Exception:
        if work:
            return
        raise
    for item in work:
        try:
            work()
        except Exception:
            if item:
                continue
            raise
        try:
            work()
        except Exception:
            if item:
                break
            raise
    try:
        work()
    except Exception:
        def nested():
            return 1

        class Scoped:
            def method(self):
                return 2

        raise
    try:
        work()
    except Exception:
        value = lambda: 1
        raise
    try:
        work()
    except Exception:
        raise
        pass


def decorated_body(work):
    try:
        work()
    except Exception:
        @decorator  # above the def
        def first():
            pass
    try:
        work()
    except Exception:
        @decorator
        # between decorator and def
        def second():
            pass
    try:
        work()
    except Exception:
        @decorator
        class Third:  # on the class line
            pass
    try:
        work()
    except Exception:
        (
            work
        )  # after a parenthesized body
    try:
        value = """text
        more"""  # after a string
    except Exception:
        pass
    try:
        value = """text
more"""  # after a string
    except Exception:
        pass
    try:
        work(  # open
            1,  # item
        )  # close
    except Exception:
        pass


try:
    run(None)
# module level reason
except Exception:
    pass
