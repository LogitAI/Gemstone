import ast
import math
import operator
import re
from typing import Union


MAX_BITS = 100_000  # largest integer a calculation may produce (about 30 000 digits)
MAX_EXPRESSION_LENGTH = 1_000


class _TooLarge(Exception):
    pass


def _check(value):
    if isinstance(value, int) and value.bit_length() > MAX_BITS:
        raise _TooLarge()
    return value


def _pow(base, exponent):
    # Bound the work before doing it: `9**9**9**9` would otherwise never return.
    if isinstance(base, int) and isinstance(exponent, int) and exponent > 0 and abs(base) > 1:
        if exponent * base.bit_length() > MAX_BITS + base.bit_length():
            raise _TooLarge()
    return _check(pow(base, exponent))


_FUNCTIONS = {
    "abs": abs, "round": round, "pow": _pow, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "log": math.log, "log10": math.log10, "exp": math.exp,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Pow: _pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _evaluate(node):
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return _check(node.value)
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _check(_BINARY[type(node.op)](_evaluate(node.left), _evaluate(node.right)))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS
            and not node.keywords):
        return _check(_FUNCTIONS[node.func.id](*[_evaluate(arg) for arg in node.args]))
    raise ValueError("unsupported syntax")


def calculate(expression: str) -> str:
    """
    Evaluate a mathematical expression and return the result as a string.
    Supports basic arithmetic, parentheses, and common mathematical functions.

    Args:
        expression (str): The mathematical expression to evaluate.

    Returns:
        str: The result of the evaluation or an error message if the expression is invalid.
    """
    try:
        # Clean the expression
        expression = expression.replace(' ', '')
        if len(expression) > MAX_EXPRESSION_LENGTH:
            return "Error: Expression is too long"

        # Check for dangerous patterns
        dangerous_patterns = [
            r'__.*__',  # dunder methods
            r'import',  # import statements
            r'exec',    # exec function
            r'eval',    # eval function
            r'open',    # file operations
            r'input',   # input function
        ]

        for pattern in dangerous_patterns:
            if re.search(pattern, expression, re.IGNORECASE):
                return "Error: Invalid expression contains forbidden operations"

        # Validate that expression only contains allowed characters
        allowed_chars = set('0123456789+-*/().,abcdeghilmnopqrstuwx_')  # includes function names
        if not all(c in allowed_chars for c in expression.lower()):
            return "Error: Expression contains invalid characters"

        # Evaluate the parsed tree: only numbers, + - * / // **, parentheses, constants and the
        # listed functions are understood, and integer results are bounded in size.
        result = _evaluate(ast.parse(expression, mode="eval"))

        # Handle different result types
        if isinstance(result, float):
            # Round to avoid floating point precision issues
            if result.is_integer():
                return str(int(result))
            else:
                return f"{result:.10g}"  # Remove trailing zeros
        else:
            return str(result)

    except _TooLarge:
        return "Error: Result too large"
    except ZeroDivisionError:
        return "Error: Division by zero"
    except ValueError as e:
        return f"Error: Invalid value - {str(e)}"
    except OverflowError:
        return "Error: Result too large"
    except Exception as e:
        return f"Error: {str(e)}"


def add(a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
    """Add two numbers."""
    return a + b


def subtract(a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
    """Subtract b from a."""
    return a - b


def multiply(a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
    """Multiply two numbers."""
    return a * b


def divide(a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
    """Divide a by b."""
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    return a / b


def power(base: Union[int, float], exponent: Union[int, float]) -> Union[int, float]:
    """Calculate base raised to the power of exponent."""
    return base ** exponent


def square_root(number: Union[int, float]) -> float:
    """Calculate the square root of a number."""
    if number < 0:
        raise ValueError("Cannot calculate square root of negative number")
    return math.sqrt(number)


def factorial(n: int) -> int:
    """Calculate the factorial of a non-negative integer."""
    if not isinstance(n, int) or n < 0:
        raise ValueError("Factorial is only defined for non-negative integers")
    return math.factorial(n)


# Example usage and test cases
if __name__ == '__main__':
    test_expressions = [
        "2 + 3 * 4",
        "(2 + 3) * 4",
        "sqrt(16)",
        "sin(pi/2)",
        "log(e)",
        "2**3",
        "10/3",
        "abs(-5)",
    ]
    
    print("Calculator Test Results:")
    for expr in test_expressions:
        result = calculate(expr)
        print(f"{expr} = {result}")
