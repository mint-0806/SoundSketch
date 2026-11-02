# 相关工作与研究定位

本项目探索通过可控合成和试听反馈表达生活噪声听感。交互声音表达、声音模仿和反馈式合成已有研究先例；加入 LLM 并不直接证明沟通或排查效果更好。

- Lemaitre & Rocchesso (2014), *On the effectiveness of vocal imitations and verbal descriptions of sounds*, JASA. [DOI](https://doi.org/10.1121/1.4861245)。研究文字与人声模仿传达声音的效果，为“声音示意能否改善沟通”提供背景，未验证本项目的 DSP/LLM 流程。
- Cartwright & Pardo (2014), *SynthAssist: An Audio Synthesizer Programmed with Vocal Imitation*, ACM Multimedia. [作者公开论文](https://interactiveaudiolab.github.io/assets/papers/cartwright_pardo_acmmm14.pdf)。使用声音示例与候选反馈进行合成器搜索，为迭代试听提供先例。

后续如开展用户实验，应分别检验：

1. 与纯文字、固定声音库等基线相比，是否更准确地传达声音特征。
2. 在同等时间内，是否改善第三方理解、降低交流轮数或减少放弃。
3. 候选试听是否影响延迟回忆，用户认为“更像”是否对应第三方可验证的收益。
4. 若扩展到排查，真实检查结果是否支持原因假设；合成目标任务不能代替现场诊断验证。

当前没有用户实验结果、真实案例诊断准确率或商业部署结论。
