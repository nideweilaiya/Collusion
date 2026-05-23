"""MC Mod 自动化开发 — Goal 模板 + ModAnalyzer

核心思想: MC Mod 开发有高度重复的模式(物品/实体/技能/GUI/配方),
只要识别出这些模式, 就能自动生成 Goal 配置, 让 GoalRunner 闭环执行.

工作流:
  1. ModAnalyzer 扫描现有代码, 提取模式模板
  2. 你说 "加一个新技能" → 自动生成 Goal 配置 (含 collusion_route 文件集)
  3. GoalRunner 执行: 我改代码 → gradle build 验证 → 修 → 归档
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional


class ModPattern:
    """MC Mod 模式模板 — 描述一种可自动生成的代码模式"""
    
    def __init__(self, name: str, category: str, 
                 files_required: List[str],  # 需要创建/修改的文件
                 verification_cmd: str,      # 验证命令
                 template_desc: str,         # 模板描述
                 parameters: List[str] = None):  # 模板参数
        self.name = name
        self.category = category
        self.files_required = files_required
        self.verification_cmd = verification_cmd
        self.template_desc = template_desc
        self.parameters = parameters or []

    def to_goal_config(self, params: dict) -> dict:
        """生成 GoalRunner 配置"""
        cmd = self.verification_cmd
        
        # 计算 allowed_files (从已有代码 pattern 推导)
        allowed = []
        for f in self.files_required:
            allowed.append(f.replace("{name}", params.get("name", "New")))
        
        return {
            "goal_id": f"mod_{self.category}_{params.get('name', 'new').lower()}",
            "description": self.template_desc.format(**params),
            "verification": {
                "command": cmd,
                "expected_exit_code": 0,
                "max_iterations": 5,
                "timeout_seconds": 300,
            },
            "review": {
                "enabled": True,
                "agents": ["architecture"],
                "checklist": [
                    "遵循现有代码风格",
                    "不引入外部依赖",
                    "注册到正确的 Registry",
                    "添加对应的 JSON 资源文件",
                ],
            },
            "constraints": {
                "allowed_files": allowed,
                "forbidden_files": ["src/main/java/com/aiworkbench/companion/AICompanionMod.java"],
            },
        }


class ModAnalyzer:
    """MC Mod 代码分析器 — 从现有代码提取模式"""

    def __init__(self, mod_root: str):
        self.root = Path(mod_root)
        self.java_root = self.root / "src" / "main" / "java" / "com" / "aiworkbench" / "companion"
        self.resources_root = self.root / "src" / "main" / "resources"

    def analyze(self) -> dict:
        """全量分析 Mod 结构"""
        return {
            "n_java_files": len(list(self.root.rglob("*.java"))),
            "patterns": self._extract_patterns(),
            "goals": self._build_goal_templates(),
        }

    def _extract_patterns(self) -> List[ModPattern]:
        """从现有代码提取模式模板"""
        patterns = []

        # 1. 技能 Action 模式
        action_dir = self.java_root / "skill" / "atomic"
        if action_dir.exists():
            for f in action_dir.glob("*.java"):
                with open(f, "r") as fh:
                    content = fh.read()
                # 提取构造函数和 execute 方法签名
                name = f.stem
                ctors = re.findall(r'public\s+' + name + r'\s*\(', content)
                execs = re.findall(r'public\s+SkillResult\s+execute\(', content)
                if ctors and execs:
                    patterns.append(ModPattern(
                        name=name,
                        category="atomic_action",
                        files_required=["src/main/java/.../skill/atomic/{name}.java"],
                        verification_cmd=f"gradle test --tests *{name}* 2>nul || gradle build 2>nul",
                        template_desc=f"创建新的原子技能 Action。参考 {name} 的结构",
                        parameters=["name", "description"],
                    ))

        # 2. GUI Screen 模式
        gui_dir = self.java_root / "client" / "gui"
        if gui_dir.exists():
            names = [f.stem for f in gui_dir.glob("*Screen.java")]
            if names:
                patterns.append(ModPattern(
                    name="GUIScreen",
                    category="gui",
                    files_required=[
                        f"src/main/java/.../client/gui/{{name}}Screen.java",
                        f"src/main/java/.../inventory/{{name}}Container.java",
                    ],
                    verification_cmd="gradle build",
                    template_desc=f"创建新的 GUI 界面。参考现有 Screen: {names[0]}",
                    parameters=["name", "title"],
                ))

        # 3. 命令模式
        cmd_dir = self.java_root / "command"
        if cmd_dir.exists():
            names = [f.stem for f in cmd_dir.glob("*Commands.java")]
            if names:
                patterns.append(ModPattern(
                    name="Command",
                    category="command",
                    files_required=[f"src/main/java/.../command/{{name}}Commands.java"],
                    verification_cmd="gradle build",
                    template_desc=f"创建新的命令。参考 {names[0]}",
                    parameters=["name", "description"],
                ))

        # 4. 物品模式
        item_dir = self.java_root / "item"
        item_path = item_dir / "ItemInit.java"
        if item_path.exists():
            with open(item_path, "r") as fh:
                content = fh.read()
            reg_pattern = re.findall(r'REGISTRY\.register\(\"(\w+)\"', content)
            if reg_pattern:
                patterns.append(ModPattern(
                    name="Item",
                    category="item",
                    files_required=[
                        f"src/main/java/.../item/{{name}}.java",
                        f"src/main/java/.../item/ItemInit.java",
                        f"src/main/resources/assets/aicompanion/models/item/{{name}}.json",
                        f"src/main/resources/data/aicompanion/recipes/{{name}}.json",
                    ],
                    verification_cmd="gradle build",
                    template_desc="创建新的物品。参考现有注册模式",
                    parameters=["name", "display_name", "texture"],
                ))

        return patterns

    def _build_goal_templates(self) -> Dict[str, dict]:
        """生成可直接使用的 Goal 配置 (只填参数名)"""
        templates = {}
        
        templates["add_atomic_action"] = {
            "goal_id": "mod_action_{name}",
            "description": "为 AICompanion 创建新的原子技能 {name}: {description}",
            "verification": {
                "command": "gradle build",
                "expected_exit_code": 0,
                "max_iterations": 5,
                "timeout_seconds": 300,
            },
            "review": {"enabled": True, "agents": ["architecture"], "checklist": [
                "实现 AtomicAction 接口",
                "注册到 SkillEngine",
                "添加单元测试",
            ]},
            "constraints": {
                "allowed_files": ["src/main/java/.../skill/atomic/{name}.java",
                                  "src/main/java/.../skill/SkillEngine.java",
                                  "src/test/java/.../skill/{name}Test.java"],
                "forbidden_files": ["src/main/java/.../AICompanionMod.java"],
            },
            "templates": {
                "main_class": f"""package com.aiworkbench.companion.skill.atomic;

import com.aiworkbench.companion.skill.SkillResult;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.level.Level;

/**
 * {{description}}
 */
public class {{name}} extends AtomicAction {{
    
    public {{name}}() {{
        super("{{key}}");
    }}
    
    @Override
    public SkillResult execute(LivingEntity entity, Level level) {{
        // TODO: 实现具体行为
        return SkillResult.success();
    }}
}}""",
                "test_class": f"""package com.aiworkbench.companion.skill.atomic;

import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;

class {{name}}Test {{
    
    @Test
    void testExecute() {{
        {{name}} action = new {{name}}();
        assertNotNull(action);
        assertEquals("{{key}}", action.getId());
    }}
}}""",
            },
        }

        templates["add_item"] = {
            "goal_id": "mod_item_{name}",
            "description": "创建新的物品 {name}: {display_name}",
            "verification": {
                "command": "gradle build",
                "expected_exit_code": 0,
                "max_iterations": 5,
                "timeout_seconds": 300,
            },
            "review": {"enabled": True, "checklist": [
                "物品类正确实现",
                "注册到 ItemInit",
                "有对应的 model JSON",
                "有纹理文件（如有）",
            ]},
            "constraints": {
                "allowed_files": ["src/main/java/.../item/{name}.java",
                                  "src/main/java/.../item/ItemInit.java",
                                  "src/main/resources/**"],
                "forbidden_files": ["src/main/java/.../AICompanionMod.java",
                                    "src/main/java/.../entity/"],
            },
        }

        return templates

    def suggest_goal(self, task_desc: str) -> Optional[dict]:
        """根据自然语言描述推荐最匹配的 Goal 模板"""
        task_lower = task_desc.lower()
        
        patterns_map = {
            "技能": "add_atomic_action",
            "action": "add_atomic_action",
            "物品": "add_item",
            "item": "add_item",
            "gui": "gui",
            "界面": "gui",
            "命令": "command",
            "command": "command",
        }

        for keyword, template_name in patterns_map.items():
            if keyword in task_lower:
                template = self._build_goal_templates().get(template_name)
                if template:
                    return {
                        "template": template_name,
                        "goal_config": template,
                        "suggestion": f"检测到关键词「{keyword}」，已匹配模板 {template_name}",
                    }

        # 通用 fallback
        return {
            "template": "custom",
            "goal_config": None,
            "suggestion": "未匹配到已知模板, 将使用通用 Goal 配置。",
        }
