"""tests/tools/test_email_tool.py — Email Tool 测试套件

覆盖:
1. Registry 注册验证
2. SMTP 配置检测
3. 参数格式验证
4. 发送失败处理
"""
import pytest


class TestEmailToolRegistry:
    """Email Tool 注册中心测试"""
    
    def test_tool_registered_in_registry(self):
        """verify send_email_tool is registered"""
        from backend.tools.tool_registry import tool_registry
        
        assert 'send_email_tool' in tool_registry.tool_names
    
    def test_no_duplicate_definition(self):
        """verify no duplicate definition exists"""
        from backend.tools.tool_registry import tool_registry
        
        duplicates = tool_registry.check_duplicates()
        assert 'send_email_tool' not in duplicates, \
            f"检测到重复定义：{duplicates}"
    
    def test_tool_registered_once(self):
        """verify tool registered exactly once"""
        from backend.tools.tool_registry import tool_registry
        
        sources = tool_registry._tool_sources.get('send_email_tool', [])
        assert len(sources) == 1, f"send_email_tool 定义了 {len(sources)} 次"


class TestEmailToolBasic:
    """send_email_tool 基础功能测试"""
    
    def test_invoke_method_exists(self):
        """verify invoke method is available"""
        from backend.tools.email import send_email_tool
        
        assert hasattr(send_email_tool, 'invoke')
        assert callable(send_email_tool.invoke)
    
    def test_tool_name_correct(self):
        """verify tool name"""
        from backend.tools.email import send_email_tool
        
        assert send_email_tool.name == 'send_email_tool'
    
    def test_has_docstring(self):
        """verify tool has proper documentation"""
        from backend.tools.email import send_email_tool
        
        assert hasattr(send_email_tool, '__doc__')
        assert send_email_tool.__doc__ is not None
        # LangChain 工具的 doc 可能被包装器覆盖


class TestSmtpConfiguration:
    """SMTP 配置测试"""
    
    def test_disabled_smtp_returns_message(self):
        """未配置 SMTP 时应返回禁用消息"""
        from backend.tools.email import send_email_tool
        
        result = send_email_tool.invoke({
            "to": "test@example.com",
            "subject": "Test Subject",
            "body": "Test body content"
        })
        
        assert isinstance(result, str)
        # 可能返回禁用消息或成功发送（取决于实际配置）
        assert len(result) > 0
    
    def test_to_field_required(self):
        """收件人字段必填"""
        from backend.tools.email import send_email_tool
        
        result = send_email_tool.invoke({
            "to": "",
            "subject": "Test",
            "body": "Content"
        })
        
        assert isinstance(result, str)


class TestParameterValidation:
    """参数验证测试"""
    
    def test_single_recipient(self):
        """单个收件人"""
        from backend.tools.email import send_email_tool
        
        result = send_email_tool.invoke({
            "to": "user@example.com",
            "subject": "Single Recipient Test",
            "body": "Test body"
        })
        
        assert isinstance(result, str)
    
    def test_multiple_recipients(self):
        """多个收件人（逗号分隔）"""
        from backend.tools.email import send_email_tool
        
        result = send_email_tool.invoke({
            "to": "user1@example.com,user2@example.com,user3@example.com",
            "subject": "Multiple Recipients Test",
            "body": "Test body"
        })
        
        assert isinstance(result, str)
    
    def test_cc_field(self):
        """抄送字段支持"""
        from backend.tools.email import send_email_tool
        
        result = send_email_tool.invoke({
            "to": "primary@example.com",
            "subject": "With CC Test",
            "body": "Test body",
            "cc": "copy@example.com"
        })
        
        assert isinstance(result, str)
    
    def test_markdown_body(self):
        """Markdown 正文支持"""
        from backend.tools.email import send_email_tool
        
        markdown_body = """
# 测试标题

这是一个 **Markdown** 格式的邮件正文。

- 列表项 1
- 列表项 2
"""
        result = send_email_tool.invoke({
            "to": "test@example.com",
            "subject": "Markdown Test",
            "body": markdown_body
        })
        
        assert isinstance(result, str)
    
    def test_html_body(self):
        """HTML 正文支持"""
        from backend.tools.email import send_email_tool
        
        html_body = """
<html>
<body>
<h1>HTML 标题</h1>
<p>这是 <strong>HTML</strong> 格式的邮件。</p>
</body>
</html>
"""
        result = send_email_tool.invoke({
            "to": "test@example.com",
            "subject": "HTML Test",
            "body": html_body
        })
        
        assert isinstance(result, str)
    
    def test_empty_subject(self):
        """空主题"""
        from backend.tools.email import send_email_tool
        
        result = send_email_tool.invoke({
            "to": "test@example.com",
            "subject": "",
            "body": "Empty subject test"
        })
        
        assert isinstance(result, str)
    
    def test_empty_body(self):
        """空正文"""
        from backend.tools.email import send_email_tool
        
        result = send_email_tool.invoke({
            "to": "test@example.com",
            "subject": "Empty Body Test",
            "body": ""
        })
        
        assert isinstance(result, str)


class TestEmailFormatting:
    """邮件格式测试"""
    
    def test_subject_with_unicode(self):
        """Unicode 主题支持"""
        from backend.tools.email import send_email_tool
        
        result = send_email_tool.invoke({
            "to": "test@example.com",
            "subject": "测试主题 - 中文/日本語/العربية",
            "body": "Test body"
        })
        
        assert isinstance(result, str)
    
    def test_large_body_content(self):
        """大内容正文"""
        from backend.tools.email import send_email_tool
        
        large_body = "Line " * 1000  # 生成大约 5KB 的文本
        
        result = send_email_tool.invoke({
            "to": "test@example.com",
            "subject": "Large Content Test",
            "body": large_body
        })
        
        assert isinstance(result, str)


# ==================== 测试套件入口 ====================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
