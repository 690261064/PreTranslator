# DLX 修复版说明 (deeplx-fixed.exe)

## 这是什么

本仓库内置的 `deeplx-fixed.exe` 是 [OwO-N1/DeepLX](https://github.com/OwO-N1/DeepLX)
的重新编译版, 与官方版**只差一行代码**: 删除了 `SetTLSFingerprintIOS()` 调用。

## 为什么需要修复

2026-10 下旬起, 官方版 DLX 发出的**所有**翻译请求都被 DeepL 以 429 拒绝,
无论 IP、代理或频率如何。

- **根因不是 IP 封锁**: DLX 用 utls 伪装的 iOS TLS 握手指纹
  (`SetTLSFingerprintIOS()`) 被 DeepL 约 10 月 23 日上线的风控规则拉黑,
  任何 IP 都返回 429。响应报文里 "your IP has been blocked" 是误导信息。
- **修复 = 删除一行**:

  ```
  文件: translate/translate.go   函数: newOneshotClient
  删除: .SetTLSFingerprintIOS().
  ```

- 上游相关 issue: OwO-N1/DeepLX#248 ("It was working great until yesterday")。
- 等上游修复后, 直接换回官方新版即可, 本工具不依赖此 exe 的任何特殊行为。

## 重新编译方法 (如需自行修改)

```bash
# 需要 Go 1.21+ (模块会自动拉取工具链)
git clone https://github.com/OwO-N1/DeepLX DLX-main
cd DLX-main
# 编辑 translate/translate.go: 在 newOneshotClient 里删掉 .SetTLSFingerprintIOS() 链式调用
go build -o deeplx-fixed.exe .
```

Windows 下交叉编译:

```bash
GOOS=windows GOARCH=amd64 go build -o deeplx-fixed.exe .
```

## 更省事的替代方案

其实**不必依赖匿名 DLX 通道**: 在 [deepl.com](https://www.deepl.com/pro-api)
免费注册一个 API Free 账号, 把密钥填进本工具的「API密钥」框即可——
官方免费 API 限速宽松、实测 20+ 句/秒, 每号每月 50 万字符起 (实测新注册有 100 万),
是匿名通道的 20 倍左右。DLX 只是没密钥时的兜底。
