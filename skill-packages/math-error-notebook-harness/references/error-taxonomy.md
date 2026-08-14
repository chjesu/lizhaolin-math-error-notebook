# Error-cause taxonomy

Use one primary cause and explain the evidence. Mention secondary causes in `cause_detail` rather than inventing compound codes.

| Code | Chinese label | Use when | Do not use when |
|---|---|---|---|
| `knowledge_gap` | 知识点未掌握 | The student cannot recall or state a required definition, theorem, or formula | The formula is known but applied outside its conditions |
| `concept_confusion` | 概念理解不准确 | Two related concepts are confused or a definition's meaning is wrong | The issue is a one-off arithmetic slip |
| `formula_condition` | 公式或定理使用条件遗漏 | A theorem is used without satisfying domain, sign, independence, range, or geometry conditions | The chosen theorem is wholly irrelevant |
| `method_choice` | 解题思路选择错误 | The chosen route cannot reach the result or is mismatched to the structure | A valid route contains a later local error |
| `reasoning_gap` | 推理或步骤跳跃 | A conclusion does not follow from the previous step or a key implication is missing | The missing work is merely concise but valid |
| `algebra_transform` | 代数变形错误 | Expansion, factorization, transposition, substitution, or equivalence is invalid | Pure numerical arithmetic is the only issue |
| `calculation` | 计算错误 | The mathematical plan is valid and a numeric or symbolic calculation is wrong | The computation followed a wrong formula |
| `misreading` | 审题错误 | A condition, requested quantity, unit, range, or diagram label was read incorrectly or omitted | The condition was read but misunderstood conceptually |
| `incomplete_cases` | 漏解或分类不完整 | A branch, root, sign, endpoint, special value, or case is missing | There is only one valid case |
| `expression` | 表达或书写不规范 | The reasoning may be right but notation, units, domain, proof wording, or final form is unacceptable | The mathematical conclusion itself is wrong |
| `careless` | 有证据支持的粗心错误 | A nearby correct step makes a transcription/sign/copy slip evident | There is no direct evidence; never use as a generic fallback |
| `unclear` | 信息不足 | Image or work is incomplete, ambiguous, or unreadable enough to block diagnosis | The evidence supports a more specific cause |

Confidence guidance:

- `0.90–1.00`: the first wrong step and cause are directly visible.
- `0.70–0.89`: strong evidence with a small interpretive gap.
- `0.50–0.69`: plausible but needs student confirmation.
- `<0.50`: use `unclear` and request missing evidence.
