"""v0.10 · C86 · F70/N34/N37（任务 T110）— 命令注册中心。

分层铁律：纯叶子模块，仅 stdlib + 同包 spec。
非线程安全（同 ToolRegistry：启动期构造、之后只读）。
"""

from __future__ import annotations

from wentian.commands.spec import CommandSpec

__all__ = ["CommandRegistry"]


class CommandRegistry:
    """按注册顺序存储 :class:`~wentian.commands.spec.CommandSpec`，
    支持名称/别名大小写不敏感的查找与补全。

    冲突规则：已注册的规范名或别名与新条目的任何 key 重叠时
    :meth:`register` 立即 raise :class:`ValueError`。

    Thread-safety: 不要求（启动构造、之后只读，同 ToolRegistry 惯例）。
    """

    def __init__(self) -> None:
        # key: 规范名 / 别名（均小写） → CommandSpec
        self._by_key: dict[str, CommandSpec] = {}
        # 注册顺序列表（去重，每条 spec 只出现一次）
        self._order: list[CommandSpec] = []

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def register(self, spec: CommandSpec) -> None:
        """注册一条命令规格。

        Parameters
        ----------
        spec:
            待注册的 :class:`CommandSpec`。

        Raises
        ------
        ValueError
            ``spec.name`` 或 ``spec.aliases`` 中任意 key 已被占用。
        """
        all_keys = [spec.name.lower()] + [a.lower() for a in spec.aliases]
        for key in all_keys:
            if key in self._by_key:
                raise ValueError(
                    f"命令 key {key!r} 已被注册（规范名或别名冲突）。"
                    f" 新命令: {spec.name!r}，已有命令: {self._by_key[key].name!r}。"
                )
        for key in all_keys:
            self._by_key[key] = spec
        self._order.append(spec)

    def unregister(self, name: str) -> None:
        """移除规范名 == *name*（大小写不敏感）的命令。

        连同它的全部别名 key 与有序可见列表中的条目一并删除。命令不存在时
        为 no-op（不抛）。

        v0.11 · C107b（任务 T134b）— 为 Skill 斜杠命令的冲突替换（PROMPT 同名
        覆盖）与 ``/skills reload`` 的旧命令清理提供「可逆注册」基础。
        Additive：不改 register/lookup/visible/completions 既有语义。
        """
        key = name.lower()
        spec = self._by_key.get(key)
        if spec is None:
            return
        # 摘掉该 spec 的所有 key（规范名 + 别名）——按 spec 身份比对，避免误删
        # 与其别名同名的其它命令（注册期冲突检查已保证 key 唯一映射，这里以
        # spec 身份兜底）。
        all_keys = [spec.name.lower()] + [a.lower() for a in spec.aliases]
        for k in all_keys:
            if self._by_key.get(k) is spec:
                del self._by_key[k]
        # 有序列表里按身份移除（每条 spec 仅出现一次）。
        self._order[:] = [s for s in self._order if s is not spec]

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> CommandSpec | None:
        """按名称（大小写不敏感）查找命令，未找到返回 ``None``。"""
        return self._by_key.get(name.lower())

    def visible(self) -> list[CommandSpec]:
        """返回 ``hidden=False`` 的命令，按注册顺序。"""
        return [s for s in self._order if not s.hidden]

    def all(self) -> list[CommandSpec]:
        """返回全部命令，按注册顺序。"""
        return list(self._order)

    def completions(self, prefix: str) -> list[CommandSpec]:
        """返回可见命令中规范名以 *prefix* 开头的 :class:`CommandSpec` 列表（按注册顺序）。

        Parameters
        ----------
        prefix:
            补全前缀（大小写不敏感）；空串返回全部可见命令的 spec。
        """
        p = prefix.lower()
        return [s for s in self._order if not s.hidden and s.name.startswith(p)]
