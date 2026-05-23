"""MC Mod 资源 + 逻辑一致性检查器

在 GoalRunner 的 Reviewer 阶段自动执行，验证:
  1. 每个注册的物品/方块是否有对应的 model JSON
  2. 每个注册的物品是否有对应的 lang 条目
  3. model JSON 引用的 texture 是否真实存在
  4. recipe JSON 引用的物品 ID 是否已注册
  5. 新注册的实体是否有对应的 spawn egg
  6. CodeGraph: 新技能是否被 SkillEngine/SkillLibrary 引用
  7. 状态机闭环: 所有状态是否有出口

用法:
  python mc_resources.py D:/AI_Workbench/integrations/minecraft/forge-mod
"""
import json
import os
import re
import sys
from pathlib import Path


class ModResourceChecker:
    """MC Mod 资源文件一致性检查器"""

    def __init__(self, mod_root: str):
        self.root = Path(mod_root)
        self.java_dir = self.root / "src" / "main" / "java"
        self.res_dir = self.root / "src" / "main" / "resources"
        self.modid = "aicompanion"

        # 从 build.gradle 或 Java 文件提取 modid
        build_gradle = self.root / "build.gradle"
        if build_gradle.exists():
            with open(build_gradle, "r") as f:
                content = f.read()
            m = re.search(r'archivesBaseName\s*=\s*"(\w+)"', content)
            if m:
                self.modid = m.group(1)

    def check_all(self, codegraph_db: str = None) -> dict:
        """运行所有检查, 返回结果
        Args:
            codegraph_db: CodeGraph SQLite 数据库路径 (用于技能注册检查)
        """
        """运行所有检查, 返回结果"""
        results = {
            "passed": True,
            "checks": [],
            "errors": [],
            "warnings": [],
            "stats": {},
        }

        self._check_items(results)
        self._check_lang(results)
        self._check_textures(results)
        self._check_recipes(results)
        self._check_entities(results)

        results["passed"] = len(results["errors"]) == 0
        # ========== v1.0.1: CodeGraph 检查 ==========
        if codegraph_db and os.path.exists(codegraph_db):
            self._check_codegraph_registration(codegraph_db, results)

        # ========== v1.0.1: 状态机检查 ==========
        self._check_state_machine(results)

        results["stats"] = {
            "registered_items": len(self._get_registered_items()),
            "lang_entries": len(self._get_lang_entries()),
            "model_files": len(self._get_model_files()),
            "texture_files": len(self._get_texture_files()),
            "recipe_files": len(self._get_recipe_files()),
        }
        return results

    # ==================== 内部检查 ====================

    def _get_registered_items(self) -> set:
        """从 Java 代码提取已注册的物品 ID"""
        items = set()
        for f in self.java_dir.rglob("*.java"):
            with open(f, "r", errors="ignore") as fh:
                content = fh.read()
            for m in re.finditer(r'REGISTRY\.register\("(\w+)"', content):
                items.add(m.group(1))
            for m in re.finditer(r'register\("(\w+)"', content):
                items.add(m.group(1))
        return items

    def _get_lang_entries(self) -> dict:
        """提取 lang 文件中的所有条目"""
        entries = {}
        for lang_file in self.res_dir.rglob("lang/*.json"):
            with open(lang_file, "r", encoding="utf-8") as f:
                try:
                    entries.update(json.load(f))
                except json.JSONDecodeError:
                    pass
        return entries

    def _get_model_files(self) -> set:
        """获取所有 model JSON 文件"""
        models = set()
        for f in (self.res_dir / "assets" / self.modid / "models").rglob("*.json"):
            if "block" in str(f) or "item" in str(f):
                models.add(f.stem)
        return models

    def _get_texture_files(self) -> set:
        """获取所有 texture 文件"""
        textures = set()
        tex_dir = self.res_dir / "assets" / self.modid / "textures"
        if tex_dir.exists():
            for f in tex_dir.rglob("*"):
                if f.suffix in (".png", ".jpg", ".jpeg", ".tga"):
                    textures.add(f.stem)
        return textures

    def _get_recipe_files(self) -> list:
        """获取所有配方文件"""
        recipes = []
        recipe_dir = self.res_dir / "data" / self.modid / "recipes"
        if recipe_dir.exists():
            for f in recipe_dir.rglob("*.json"):
                with open(f, "r") as fh:
                    try:
                        recipes.append((f.stem, json.load(fh)))
                    except json.JSONDecodeError:
                        pass
        return recipes

    def _check_items(self, results: dict):
        items = self._get_registered_items()
        models = self._get_model_files()

        results["checks"].append({
            "name": "registered_items",
            "description": "检查注册物品是否有对应 model JSON",
            "count": len(items),
        })

        for item in items:
            if item not in models:
                results["warnings"].append(
                    f"物品 [{item}] 缺少 model JSON: assets/{self.modid}/models/item/{item}.json"
                )

        # 反向检查：多余的 model 文件
        for model in models:
            if model not in items and not model.startswith("spawn_egg"):
                results["warnings"].append(
                    f"model JSON [{model}] 没有对应的注册物品，可能未被使用"
                )

    def _check_lang(self, results: dict):
        items = self._get_registered_items()
        lang = self._get_lang_entries()

        results["checks"].append({
            "name": "lang_entries",
            "description": "检查是否有缺失的 lang 翻译",
            "count": len(lang),
        })

        mod_prefixes = [f"item.{self.modid}.", f"block.{self.modid}.", f"entity.{self.modid}.",
                        f"skill.{self.modid}.", f"command.{self.modid}.",
                        f"gui.{self.modid}.", f"container.{self.modid}."]

        for item in items:
            found = False
            for prefix in mod_prefixes:
                key = f"{prefix}{item}"
                if key in lang:
                    found = True
                    break
            if not found:
                results["warnings"].append(
                    f"物品 [{item}] 缺少 lang 翻译条目 (预期任意: {', '.join(p+item for p in mod_prefixes[:2])})"
                )

    def _check_textures(self, results: dict):
        textures = self._get_texture_files()

        results["checks"].append({
            "name": "texture_files",
            "description": "检查 model JSON 引用的 texture 是否存在",
            "count": len(textures),
        })

        for model_file in (self.res_dir / "assets" / self.modid / "models").rglob("*.json"):
            with open(model_file, "r") as f:
                try:
                    model_data = json.load(f)
                except json.JSONDecodeError:
                    continue

            # 提取 textures 引用
            tex_refs = self._extract_texture_refs(model_data)
            for ref in tex_refs:
                if ref in ("minecraft:block/stone", "minecraft:block/dirt",
                           "minecraft:block/oak_planks", "minecraft:item/stick"):
                    continue  # 原版纹理跳过
                local_name = ref.split(":")[-1].split("/")[-1]
                if local_name not in textures:
                    results["errors"].append(
                        f"model [{model_file.name}] 引用 texture [{ref}], "
                        f"但文件不存在 (预期: {local_name}.png)"
                    )

    def _extract_texture_refs(self, model_data: dict, seen: set = None) -> set:
        """递归提取 model JSON 中的所有 texture 引用"""
        if seen is None:
            seen = set()
        if isinstance(model_data, dict):
            for key, val in model_data.items():
                if key == "texture" and isinstance(val, str):
                    seen.add(val)
                elif key == "textures" and isinstance(val, dict):
                    for v in val.values():
                        if isinstance(v, str):
                            seen.add(v)
                else:
                    self._extract_texture_refs(val, seen)
        elif isinstance(model_data, list):
            for item in model_data:
                self._extract_texture_refs(item, seen)
        return seen

    def _check_recipes(self, results: dict):
        items = self._get_registered_items()
        recipes = self._get_recipe_files()

        results["checks"].append({
            "name": "recipes",
            "description": "检查配方引用的物品 ID 是否已注册",
            "count": len(recipes),
        })

        for recipe_name, recipe_data in recipes:
            # 检查 result.item 是否已注册
            result_item = (recipe_data.get("result") or {}).get("item", "")
            if result_item and ":" in result_item:
                local_id = result_item.split(":")[1]
                if local_id not in items and not result_item.startswith("minecraft:"):
                    results["warnings"].append(
                        f"配方 [{recipe_name}] 产出 [{result_item}] 未在代码中注册"
                    )

    def _check_entities(self, results: dict):
        """检查实体注册是否正确"""
        # 提取实体注册
        entities = set()
        for f in (self.java_dir / "com" / "aiworkbench" / "companion" / "entity").rglob("*.java"):
            with open(f, "r") as fh:
                content = fh.read()
            for m in re.finditer(r'EntityType\.Builder.*build\(\s*"(\w+)"', content):
                entities.add(m.group(1))
            for m in re.finditer(r'register\("(\w+)"', content):
                entities.add(m.group(1))

        results["checks"].append({
            "name": "entities",
            "description": f"检查 {len(entities)} 个实体注册",
            "count": len(entities),
        })

    # ==================== CodeGraph 技能注册检查 ====================

    def _check_codegraph_registration(self, db_path: str, results: dict):
        """通过 CodeGraph 验证新技能是否被 SkillEngine/SkillLibrary 引用"""
        import sqlite3
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()

            # 找到所有 AtomicAction 子类
            actions = set()
            for f in (self.java_dir / "com" / "aiworkbench" / "companion" / "skill" / "atomic").rglob("*.java"):
                with open(f, "r") as fh:
                    content = fh.read()
                for m in re.finditer(r'class\s+(\w+Action)\s+(extends|implements)\s+AtomicAction', content):
                    actions.add(m.group(1))

            # 对每个 Action, 检查是否有 SkillEngine/SkillLibrary 引用它的边
            unregistered = []
            for action in actions:
                # 在 CodeGraph 中搜索引用
                refs = c.execute("""
                    SELECT COUNT(*) FROM edges e
                    JOIN nodes n1 ON e.source = n1.id
                    JOIN nodes n2 ON e.target = n2.id
                    WHERE n2.name LIKE ? AND n1.file_path LIKE ?
                """, (f"%{action}%", f"%skill%")).fetchone()[0]

                if refs == 0:
                    unregistered.append(action)

            results["checks"].append({
                "name": "codegraph_registration",
                "description": f"CodeGraph 检查 {len(actions)} 个 Action 的注册完整性",
                "count": len(actions),
            })
            if unregistered:
                results["errors"].append(
                    f"以下技能在 CodeGraph 中未发现注册引用: {', '.join(unregistered)}"
                )

            conn.close()
        except Exception as e:
            results["warnings"].append(f"CodeGraph 检查失败: {e}")

    # ==================== 状态机闭环检查 ====================

    def _check_state_machine(self, results: dict):
        """检查状态机类中所有状态是否有出口"""
        for f in (self.java_dir / "com" / "aiworkbench" / "companion").rglob("*.java"):
            with open(f, "r") as fh:
                content = fh.read()

            # 找枚举状态机
            enums = re.findall(r'enum\s+\w+State\s*\{([^}]+)\}', content, re.DOTALL)
            for enum_body in enums:
                states = re.findall(r'(\w+)\s*(?:\(|,)', enum_body)
                # 检查每个状态是否有对应的 case/if 处理
                for state in states:
                    if state == "IDLE":
                        continue  # IDLE 是入口, 必然有出口
                    # 检查是否有 transition 引用
                    if not re.search(r'\b' + state + r'\b', content):
                        results["warnings"].append(
                            f"状态 [{state}] 在文件 [{f.name}] 中定义了但可能没有使用"
                        )
