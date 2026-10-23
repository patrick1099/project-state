"""Port：判断器接口。正式实现是 adapters/claude_judge.py，测试里注入假实现。"""


class JudgeError(Exception):
    """判断失败。retry=False 表示同一对话版本下次也不会成功（如工作目录已不存在）。"""

    def __init__(self, msg, retry=True):
        super().__init__(msg)
        self.retry = retry


class Judge:
    """给一个判断请求（core.make_request 的结果），返回原始字典：
    mode 为 recap/resume 时是 {status, title, stopped_at, owner}，由 core.clean_verdict 校验；
    mode 为 owner（只补判归属）时是 {owner}，由 core.clean_owner 校验。失败抛 JudgeError。
    """

    def judge(self, request: dict) -> dict:
        raise NotImplementedError