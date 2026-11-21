"""接口错误：带 HTTP 状态码，工作台把它原样回给页面。"""


class APIError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
