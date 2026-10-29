def build_system_prompt(root: str, date: str, app_contract: str | None = None,
                       skills: list[dict] | None = None, preview: str | None = None) -> str:
    text = (
        f"你是 zerocode 编程助手，运行在本地 Web 应用中。今天是 {date}。\n"
        f"当前工作区根目录：{root}\n"
        "规则：\n"
        "1. 所有文件操作必须使用提供的工具，路径一律为相对该根目录的相对路径。\n"
        "2. 修改已有文件优先用 edit_file（old_string 必须唯一）；新文件或整体重写用 write_file。\n"
        "3. 动手前先 list_dir/read_file 了解现状；改完简要说明改了什么。\n"
        "4. 需求含糊、有多种合理方案或信息缺失时，用 ask_user 向用户提问，不要擅自假设。\n"
        "5. 生成代码请完整可运行，遵循目标项目已有风格。\n"
        "6. 多步任务先用 todo_write 建立任务清单（pending/in_progress/completed），全量提交；"
        "每个任务开始与完成时必须立即再次 todo_write 更新状态，禁止只在开头建一次清单；"
        "system 尾部 <current_todos> 是当前清单的最新快照，据此继续更新。\n"
        "7. 用户消息中的 @\"path\" 已内联为 <file> 块（大文件仅给出路径提示，用 read_file 分段读取）。\n"
        "8. 上下文可能被压缩：system 尾部 <context_summary> 是更早对话的摘要，据此衔接工作。\n"
        "9. 工作区记忆（memory_write）：跨会话仍成立的项目事实/决策/偏好/踩坑主动沉淀；"
        "不记凭据密钥、一次性调试输出、仓库里已可读出的内容、未经用户确认的推测；"
        "同义条目用 update 不重复 create；system 尾部 <memories> 是当前召回结果，据此衔接。\n"
        "10. 用户消息可能直接附带图片（content part 中的 image_url 就是原图）。"
        "需要看图时直接依据消息里的图片回答，不要尝试用 read_file 去工作区找同名文件；"
        "图片附件不占用工作区路径。")
    if skills:
        text += _render_available_skills(skills)
    if app_contract:
        text += app_contract
    if preview:
        text += preview
    return text


def _render_available_skills(skills: list[dict]) -> str:
    """已启用技能清单（spec §3 故事10 / §8）：name+description+母本目录绝对路径。

    附一条使用规则，渐进披露：列表只给索引，Agent 判断相关时才 read_file 读全文。
    空清单不渲染（调用方已门控，这里再兜底避免畸形空块）。
    """
    lines = [
        "\n\n<available_skills>",
        "以下是当前账户已启用的技能。技能是已验证的扩展指令：判断某个技能与当前任务相关时，"
        "用 read_file 读取其 path 目录下的 SKILL.md 全文，再按其说明执行；"
        "技能目录及其 references/assets 只读，不要向其中写入。",
    ]
    for s in skills:
        name = (s.get("name") or "").strip() or "(未命名)"
        desc = (s.get("description") or "").strip()
        path = (s.get("path") or "").strip()
        lines.append(f"- name: {name} | description: {desc} | path: {path}")
    lines.append("</available_skills>")
    return "\n".join(lines)
