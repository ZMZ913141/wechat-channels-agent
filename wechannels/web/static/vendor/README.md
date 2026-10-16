# 第三方资源

## echarts.min.js（1.0 MB）
- **版本**：5.5.1
- **来源**：https://github.com/apache/echarts
- **许可**：Apache License 2.0
- **用途**：数据看板的图表渲染（堆叠柱、折线、横向柱）

### 为什么本地化而不是走 CDN
1. **离线可用**：CDN 挂了就整个看板白屏，本地文件不受影响。
2. **无外网依赖**：内网环境下也能正常显示图表。
3. **不引入构建链**：直接 `<script src>` 引入即可，不需要 Node/webpack。

### 升级方式
```bash
curl -sL -o wechannels/web/static/vendor/echarts.min.js \
  https://cdn.jsdelivr.net/npm/echarts@<版本号>/dist/echarts.min.js
```
升级后请回归验证 `wechannels/web/templates/dashboard.html` 中的图表是否正常渲染
（主要看堆叠柱的多系列堆叠与横向柱的 label 定位）。