"""本地验证脚本：不启动 MCP 服务，直接调用 send_email 发一封测试邮件。

用法：
    python test_email.py
"""

from dotenv import load_dotenv

load_dotenv()

from server import send_email

if __name__ == "__main__":
    result = send_email("test-user@example.com", "这是一条来自 MCP 工具的测试问题。")
    print(result)
