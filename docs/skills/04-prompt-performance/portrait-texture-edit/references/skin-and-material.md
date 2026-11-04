# 皮肤与材质：编辑用词表

**这是编辑用词，不是生成用词。** 差别只有一句：编辑时只描述「怎么变」，绝不描述「是谁」。

写"三十岁东亚女性、鹅蛋脸、深藏青西装"= 重新生成一个人。
写"肤色连续哑光、鼻翼毛孔略清晰"= 只改质感。

## 一、总原则（三条）

1. **正向描述优先于否定。** "哑光、有边界的高光"比"不要塑料"有效；否定句在部分模型上会反噬。
2. **按区域写，不按整脸写。** 全脸同质 = 假。真实的脸，每个区域细节密度不同。
3. **细节密度跟着对焦走。** 对焦平面内清晰，前后衰减；不要全脸、全发、整件衣服同等锐化。

> 依据：xxg-portrait-rebuild-light「Apply Optical Skin Realism」——vary surface detail by region（cheek softer / nose clearer / lip separate / eye area undisturbed）；"Never sharpen the whole face, every hair, clothing, and background equally"。

## 二、皮肤：三段式写法

编辑提示词里的皮肤段，按这个顺序写三件事，不要多：

```
① 底色：肤色连续、干净、哑光
② 高光：小的、有边界的反光，只落在朝向主光的平面
③ 微细节：面颊柔和 / 鼻翼略清晰 / 唇纹独立 / 眼周不动 / 随对焦衰减
```

**可直接粘贴（中文）**
```
肤色连续干净、呈哑光；鼻头与颧骨只保留小的、有边界的反光，不要全脸一层油光；
面颊毛孔柔和，鼻翼毛孔略清晰；唇纹独立可见；眼周结构不动；
细节密度随对焦与光照自然衰减，不要全脸同等锐化。
```

**可直接粘贴（英文）**
```
Clean continuous matte skin tone; small bounded highlights only on planes facing the key
light; pores soft on the cheeks, slightly clearer on the nose; separate lip texture;
eye area left untouched; microdetail fading with focus and light.
```

## 三、分区细节表

| 区域 | 该有的 | 不该有的 |
| --- | --- | --- |
| 面颊 | 柔和毛孔、轻微色差不均 | 与鼻子同等密度的毛孔、成片泛红 |
| 鼻翼/鼻头 | 略清晰的毛孔、一个小高光 | 整条鼻梁高光、镜面反光 |
| 额头 | 干净、反光呈带状 | 一片均匀高光（蜡像特征） |
| 眼周 | 结构不动、下眼睑有自然阴影 | 眼袋被"修"成平滑、眼角被抹平 |
| 唇 | 唇纹独立、有干湿分区 | 嘴唇像涂了一层蜡 |
| 下颌/颈部 | 与面部同色号、连续 | 面部白、脖子黄（贴片特征） |
| 耳 | 结构完整、血色自然 | 与面部色差过大 |

## 四、头发

| 写法 | 效果 |
| --- | --- |
| 发丝根根分明，不成块 | 破掉"一坨黑" |
| 发梢略虚，边缘有碎发 | 破掉"硬边抠图感" |
| 发根密、发梢疏，密度有渐变 | 破掉"整头均匀" |
| 头发哑光，只有一道窄高光 | 破掉"油头"（第二大破绽） |

**不要写**：`silky`、`glossy hair`、`flowing perfect hair`——这三个词直接生产塑料假发。

## 五、服装材质

材质真实感靠**结构痕迹**，不靠形容词。

| 材质 | 该写 | 别写 |
| --- | --- | --- |
| 针织/毛衣 | 织纹可见、领口轻微变形、有起球 | 柔软舒适 |
| 牛仔 | 褪色不均、缝线泛白、膝盖磨白 | 时尚做旧 |
| 皮革 | 折痕、边缘磨损、反光不均 | 高级质感 |
| 棉麻 | 褶皱有方向、透光、洗旧感 | 自然垂坠 |
| 丝绸 | 褶皱有方向、高光呈条状 | 丝滑光泽 |
| 金属配饰 | 高光呈条状、边缘有细微划痕 | 闪耀精致 |

**句式**：
```
衣料织纹可见，褶皱有方向，肩线与肘部有细微磨损；不要出现整片均匀色块。
```

## 六、禁区：别用"脏"制造真实

这一节是上一版最容易犯的错，单独列出来。

**默认不要加**（除非原图本来就有，或用户明确要求）：
- 雀斑、痘印、黑头、粗大毛孔
- 统一胶片颗粒、噪点
- 加深皱纹、眼袋、法令纹
- 随机色斑、不规则肤色

原因：这些是"另一种假"。上游明确禁止用 dirt / darkness / coarse pores / uniform grain / random color patches / exaggerated facial lines 制造真实感，要求"结果在正常尺寸下首先要读起来像一张干净的照片"。

> 依据：xxg-portrait-rebuild-light — "Do not manufacture realism with dirt, darkness, coarse pores, uniform grain, random color patches, or exaggerated facial lines. The result must first read as a clean photograph at normal size."

**原图已有的标记怎么办？** 保留，但不点名、不强化。痣还是那颗痣，不要写"脸上有一颗明显的痣"——那会让模型把它画大。

> 依据：同上 "Preserve source-existing marks as identity anchors, but do not list or generate new imperfections by default."

## 七、黑名单（编辑场景同样适用）

写进提示词就会毁掉质感的词：

```
光泽类：glowing skin、水光肌、通透、光晕、柔光、无瑕、瓷肌、奶油肌、细腻、光滑
强度类：8K、HDR、超写实、hyperrealistic、极致细节、masterpiece、award-winning
审美类：精致、完美、高级感、氛围感、flawless、perfect、beauty shot、glamour、airbrushed
风格类：cinematic、dramatic、epic、stunning、octane、unreal engine
```

> 依据：photorealistic-skill「Step 2 — Write the prompt」列的 trigger words，以及 junhao-ye/sns-realistic-portrait-prompt 明确避开的 `perfect face` / `flawless skin` / `8K ultra detailed masterpiece` / `CGI` / `doll-like`。

注意 `cinematic` 这条反直觉：它不是"电影感"，在编辑场景里它会把画面推向调色过度的商业修图。

## 八、两个即用组合

**只修皮肤（局部重绘用，最短）**
```
只优化该区域皮肤：肤色连续哑光，鼻翼毛孔略清晰，面颊柔和，
高光收成有边界的小反光，不改五官与轮廓。
```

**皮肤 + 衣料（整图编辑用）**
```
保持身份、构图、景别、光向不变，只优化材质表现：
皮肤连续哑光、区域性微细节、高光有边界；
衣料织纹可见、褶皱有方向、边缘有细微磨损；发丝根根分明、发梢略虚。
```
