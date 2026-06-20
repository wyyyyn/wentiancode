"""v0.10 · C87 · F73/N34（任务 T111）— CommandContext 协议的单元测试。

TDD 红-绿-重构：先确认因功能缺失而失败，再实现转绿。
测试策略：定义 FakeContext（实现全部协议方法的普通类），验证：
  1. isinstance(fake, CommandContext) 为 True（协议 @runtime_checkable）
  2. FakeContext 各方法调用后状态正确（夹具自测）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wentian.commands.spec import CommandSpec
    from wentian.permissions.decision import Mode


# ---------------------------------------------------------------------------
# FakeContext — 测试用假实现，记录所有调用
# ---------------------------------------------------------------------------


class FakeContext:
    """CommandContext 的测试替身，实现全部协议方法并记录调用。"""

    def __init__(self) -> None:
        from wentian.permissions.decision import Mode

        self.printed: list[object] = []
        self.sent: list[str] = []
        self._mode: Mode = Mode.DEFAULT
        self.cleared: int = 0
        self.new_session_count: int = 0
        self.listed: list[bool] = []  # all_projects 历史
        self.resumed: list[str] = []  # sid 历史
        self.switched_providers: list[str] = []
        self.compact_called: int = 0

    def print(self, renderable: object) -> None:
        self.printed.append(renderable)

    def send_user_message(self, text: str) -> None:
        self.sent.append(text)

    def get_mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode) -> None:
        self._mode = mode

    def token_usage(self) -> object | None:
        return None

    def status_line(self) -> str:
        return f"mode={self._mode.value}"

    def memory_summary(self) -> str:
        return "memory: (empty)"

    def visible_commands(self) -> list[CommandSpec]:
        return []

    def clear_context(self) -> None:
        self.cleared += 1

    def new_session(self) -> None:
        self.new_session_count += 1

    def list_sessions(self, *, all_projects: bool) -> None:
        self.listed.append(all_projects)

    def resume_session(self, sid: str) -> None:
        self.resumed.append(sid)

    def switch_provider(self, name: str) -> None:
        self.switched_providers.append(name)

    def compact_now(self) -> str:
        self.compact_called += 1
        return "compact done"


# ---------------------------------------------------------------------------
# 测试：isinstance 检查（@runtime_checkable）
# ---------------------------------------------------------------------------


def test_fake_ctx_is_command_context():
    """FakeContext 实现全部方法，isinstance(fake, CommandContext) 为 True。"""
    from wentian.commands.context import CommandContext

    fake = FakeContext()
    assert isinstance(fake, CommandContext)


def test_empty_object_is_not_command_context():
    """未实现方法的普通对象不是 CommandContext。"""
    from wentian.commands.context import CommandContext

    class Empty:
        pass

    assert not isinstance(Empty(), CommandContext)


# ---------------------------------------------------------------------------
# 测试：FakeContext 各方法调用后状态正确（夹具自测）
# ---------------------------------------------------------------------------


def test_fake_ctx_print():
    """print() 把 renderable 追加到 printed 列表。"""
    fake = FakeContext()
    fake.print("hello")
    fake.print(42)
    assert fake.printed == ["hello", 42]


def test_fake_ctx_send_user_message():
    """send_user_message() 把文本追加到 sent 列表。"""
    fake = FakeContext()
    fake.send_user_message("请帮我分析这段代码")
    assert fake.sent == ["请帮我分析这段代码"]


def test_fake_ctx_mode():
    """get_mode() / set_mode() 正确读写权限模式。"""
    from wentian.permissions.decision import Mode

    fake = FakeContext()
    assert fake.get_mode() == Mode.DEFAULT
    fake.set_mode(Mode.PLAN)
    assert fake.get_mode() == Mode.PLAN


def test_fake_ctx_token_usage_none():
    """token_usage() 在无历史时返回 None。"""
    fake = FakeContext()
    assert fake.token_usage() is None


def test_fake_ctx_status_line():
    """status_line() 返回字符串。"""
    fake = FakeContext()
    line = fake.status_line()
    assert isinstance(line, str)
    assert len(line) > 0


def test_fake_ctx_memory_summary():
    """memory_summary() 返回字符串。"""
    fake = FakeContext()
    summary = fake.memory_summary()
    assert isinstance(summary, str)


def test_fake_ctx_visible_commands():
    """visible_commands() 返回列表。"""
    fake = FakeContext()
    cmds = fake.visible_commands()
    assert isinstance(cmds, list)


def test_fake_ctx_clear_context():
    """clear_context() 递增 cleared 计数器。"""
    fake = FakeContext()
    fake.clear_context()
    fake.clear_context()
    assert fake.cleared == 2


def test_fake_ctx_new_session():
    """new_session() 递增 new_session_count。"""
    fake = FakeContext()
    fake.new_session()
    assert fake.new_session_count == 1


def test_fake_ctx_list_sessions():
    """list_sessions() 记录 all_projects 参数。"""
    fake = FakeContext()
    fake.list_sessions(all_projects=False)
    fake.list_sessions(all_projects=True)
    assert fake.listed == [False, True]


def test_fake_ctx_resume_session():
    """resume_session() 记录 sid。"""
    fake = FakeContext()
    fake.resume_session("abc-123")
    assert fake.resumed == ["abc-123"]


def test_fake_ctx_switch_provider():
    """switch_provider() 记录 provider 名称。"""
    fake = FakeContext()
    fake.switch_provider("openai")
    assert fake.switched_providers == ["openai"]


def test_fake_ctx_compact_now():
    """compact_now() 返回字符串并递增计数器。"""
    fake = FakeContext()
    result = fake.compact_now()
    assert isinstance(result, str)
    assert fake.compact_called == 1
