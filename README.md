<!-- auto-mas:i18n
{
  "display_name_i18n": {
    "zh_CN": "通用模拟器适配",
    "en": "General Emulator Adapter",
    "ja": "汎用エミュレーター"
  },
  "description_i18n": {
    "zh_CN": "通过启动程序及 ADB 连接模拟器或原生 Android 设备",
    "en": "Emulators and native Android devices through ADB",
    "ja": "ADB による Android デバイス管理"
  },
  "tags_i18n": {
    "zh_CN": [
      "模拟器"
    ],
    "en": [
      "emulator"
    ],
    "ja": [
      "エミュレーター"
    ]
  }
}
-->

# 通用模拟器适配

添加设备连接并填写 ADB 地址。可选启动程序及参数；不填写时直接连接已运行的模拟器或原生 Android 设备。

## 安装与发布

发行包为 `auto-mas-general-emulator-adapter`，插件 ID 为 `general_emulator_adapter`。
适用于 Python 3.12 和已接入游戏适配插件的 AUTO-MAS 宿主，要求 `auto-mas-core>=5.9.0b1,<6`。
通过插件市场安装时由宿主提供 core，独立安装时需要提供兼容的 SDK wheel。

首次发布前，在 [PyPI 账户的 Publishing 页面](https://pypi.org/manage/account/publishing/) 登记待定发布者。
项目名填 `auto-mas-general-emulator-adapter`，仓库所有者填 `AUTO-MAS-Project`，仓库名填 `AUTO-MAS-General-Emulator-Adapter`，工作流文件名填 `publish.yml`，环境名填 `pypi`。

分支和 PR 会构建并校验 wheel 与源码包。发布时，在默认分支的提交上打与 `pyproject.toml` 版本一致的 `v` 标签并推送；GitHub Actions 通过 Trusted Publishing 上传分发包。
