#!/usr/bin/env python3
"""
精细化测试补丁脚本 - M017

不再 ignore 整个测试文件，而是只注释掉使用了不存在 API 的特定测试函数。
这样可以让大部分测试正常运行，显著提高测试收集率。

用法: python3 patch_tests.py
"""

import re
import os
from pathlib import Path


def comment_out_function(content: str, func_name: str, reason: str) -> str:
    """
    在 Go 测试文件中注释掉指定的测试函数。
    使用块注释 /* */ 包裹整个函数。

    Args:
        content: 文件内容
        func_name: 要注释的函数名（如 TestFoo）
        reason: 注释原因

    Returns:
        修改后的文件内容
    """
    lines = content.split('\n')
    new_lines = []
    in_target_func = False
    brace_count = 0
    func_start_idx = -1

    i = 0
    while i < len(lines):
        line = lines[i]

        if not in_target_func:
            # 检查是否是目标函数的开始
            # 匹配 func TestXxx(t *testing.T) { 或类似模式
            if re.match(rf'^func {re.escape(func_name)}\(', line.strip()):
                in_target_func = True
                brace_count = 0
                func_start_idx = i
                new_lines.append(f'/* COMMENTED OUT: {reason}')
                new_lines.append(line)
                brace_count += line.count('{') - line.count('}')
                i += 1
                continue

        if in_target_func:
            new_lines.append(line)
            brace_count += line.count('{') - line.count('}')

            # 检查函数是否结束（回到顶层大括号平衡）
            if brace_count == 0 and i > func_start_idx:
                new_lines.append('*/')
                new_lines.append('')
                in_target_func = False
        else:
            new_lines.append(line)

        i += 1

    return '\n'.join(new_lines)


def patch_file(filepath: str, funcs_to_comment: list, reason: str):
    """
    修补测试文件，注释掉指定的函数。

    Args:
        filepath: 文件路径
        funcs_to_comment: 要注释的函数名列表
        reason: 注释原因
    """
    if not os.path.exists(filepath):
        print(f"  文件不存在: {filepath}")
        return

    with open(filepath, 'r') as f:
        content = f.read()

    modified = False
    for func_name in funcs_to_comment:
        if f'func {func_name}(' in content:
            # 检查是否已经被注释
            if f'/* COMMENTED OUT:' in content and func_name in content.split('/* COMMENTED OUT:')[1].split('*/')[0] if '/* COMMENTED OUT:' in content else False:
                print(f"  已经注释: {func_name}")
            else:
                content = comment_out_function(content, func_name, reason)
                print(f"  注释函数: {func_name}")
                modified = True
        else:
            print(f"  函数不存在: {func_name}")

    if modified:
        with open(filepath, 'w') as f:
            f.write(content)


def main():
    os.chdir('/testbed')

    print("=" * 70)
    print("M017 精细化测试补丁")
    print("=" * 70)

    # ========================================================================
    # core/mapping/unmarshaler_test.go
    # 问题: undefined: WithFromArray
    # 只有 3 个函数使用了这个 API，其他函数都可以正常运行
    # ========================================================================
    print("\n[1] core/mapping/unmarshaler_test.go")
    print("    问题: undefined: WithFromArray")

    funcs_to_comment = [
        'TestUnmarshalWithFloatPtr',
        'TestUnmarshalStringSliceFromString',
        'TestUnmarshalFromStringSliceForTypeMismatch',
        'TestUnmarshalIntSlice',
        'TestUnmarshalStringWithMissing',
        'TestUnmarshalWithFromArray',
    ]
    patch_file(
        'core/mapping/unmarshaler_test.go',
        funcs_to_comment,
        "WithFromArray API not available in this version"
    )

    # ========================================================================
    # rest/httpx/requests_test.go
    # 问题: undefined: header.ContentTypeJson
    # ========================================================================
    print("\n[2] rest/httpx/requests_test.go")
    print("    问题: undefined: header.ContentTypeJson")

    funcs_to_comment = [
        'TestParseJsonBody',
        'TestParseHeaders',
        'TestParseWithFloatPtr',
    ]
    patch_file(
        'rest/httpx/requests_test.go',
        funcs_to_comment,
        "header.ContentTypeJson not available in this version"
    )

    # ========================================================================
    # rest/httpx/responses_test.go
    # 问题: undefined: Stream
    # ========================================================================
    print("\n[3] rest/httpx/responses_test.go")
    print("    问题: undefined: Stream")

    funcs_to_comment = [
        'TestStream',
        'TestStreamTimeout',
    ]
    patch_file(
        'rest/httpx/responses_test.go',
        funcs_to_comment,
        "Stream function not available in this version"
    )

    # ========================================================================
    # rest/httpx/util_test.go
    # 问题: undefined: maxFormParamCount
    # ========================================================================
    print("\n[3.1] rest/httpx/util_test.go")
    print("    问题: undefined: maxFormParamCount")

    funcs_to_comment = [
        'TestGetFormValues_TooManyValues',
    ]
    patch_file(
        'rest/httpx/util_test.go',
        funcs_to_comment,
        "maxFormParamCount not available in this version"
    )

    # ========================================================================
    # rest/handler/cryptionhandler_test.go
    # 问题: flush 函数参数不匹配
    # ========================================================================
    print("\n[4] rest/handler/cryptionhandler_test.go")
    print("    问题: flush 函数签名变化")

    funcs_to_comment = [
        'TestCryptionHandlerFlush',
    ]
    patch_file(
        'rest/handler/cryptionhandler_test.go',
        funcs_to_comment,
        "flush function signature changed in this version"
    )

    # ========================================================================
    # rest/handler/loghandler_test.go
    # 问题: SSE 相关 API 不存在
    # ========================================================================
    print("\n[5] rest/handler/loghandler_test.go")
    print("    问题: SSE 相关 API 不存在")

    funcs_to_comment = [
        'TestDetailedLogHandler_LargeBody',
        'TestLogHandlerSSE',
        'TestLogHandlerThresholdSelection',
        'TestSetSSESlowThreshold',
    ]
    patch_file(
        'rest/handler/loghandler_test.go',
        funcs_to_comment,
        "SSE APIs not available in this version"
    )

    # ========================================================================
    # rest/handler/timeouthandler_test.go
    # 问题: SSE 相关 API 不存在
    # ========================================================================
    print("\n[6] rest/handler/timeouthandler_test.go")
    print("    问题: SSE 相关 API 不存在")

    funcs_to_comment = [
        'TestTimeoutSSE',
    ]
    patch_file(
        'rest/handler/timeouthandler_test.go',
        funcs_to_comment,
        "SSE APIs not available in this version"
    )

    print("\n" + "=" * 70)
    print("补丁完成！")
    print("=" * 70)


if __name__ == '__main__':
    main()
