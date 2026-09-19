import ast
import json
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext


TOOL = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "精确计算四则运算表达式。调用后约 7 秒才会返回结果；等待期间不会有中间结果。遇到需要算数的请求时使用，不要心算。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "只包含数字、加减乘除和括号的表达式，例如 (37*48)+6",
                }
            },
            "required": ["expression"],
        },
    },
}


def calculate(expression):
    if not isinstance(expression, str) or len(expression) > 120:
        raise ValueError("表达式必须是 120 字符以内的字符串")
    expression = expression.translate(str.maketrans("×÷（）－＋", "*/()-+"))
    tree = ast.parse(expression, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError("仅支持数字、括号和加减乘除")

    try:
        with localcontext() as context:
            context.prec = 28
            result = visit(tree)
    except (DivisionByZero, InvalidOperation, ZeroDivisionError) as error:
        raise ValueError("算式无效或除数为零") from error
    if abs(result) > Decimal("1e12"):
        raise ValueError("结果超出演示范围")
    return format(result, "f").rstrip("0").rstrip(".") if "." in format(result, "f") else format(result, "f")


def run_calculator(arguments):
    try:
        data = json.loads(arguments)
        expression = data["expression"]
        return json.dumps({"expression": expression, "result": calculate(expression)}, ensure_ascii=False)
    except (KeyError, TypeError, ValueError, SyntaxError) as error:
        return json.dumps({"error": str(error)}, ensure_ascii=False)
