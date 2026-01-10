# Localization (i18n) Specification

## Supported Languages

| Language | Code | Status |
|----------|------|--------|
| English | en | Primary |
| 简体中文 (Chinese) | zh-CN | Supported |

---

## Implementation Approach

### Frontend (Cockpit Plugin)

Use Cockpit's built-in translation system:

```javascript
// Load cockpit translation
cockpit.translate();

// Use translatable strings
document.getElementById("title").textContent = cockpit.gettext("GStreamer Manager");
```

**Translation files location:**
```
frontend/
└── po/
    ├── LINGUAS          # List of supported languages
    ├── gst-manager.pot  # Template
    ├── en.po            # English
    └── zh_CN.po         # Chinese
```

### Backend (Python)

Use gettext for Python strings:

```python
import gettext

# Initialize
lang = gettext.translation('gst-manager', localedir='/usr/share/locale', fallback=True)
_ = lang.gettext

# Usage
error_msg = _("Pipeline failed to start")
```

---

## Translatable Strings

### UI Labels

| English | 简体中文 |
|---------|---------|
| GStreamer Manager | GStreamer 管理器 |
| Dashboard | 仪表板 |
| Instances | 实例 |
| Create Instance | 创建实例 |
| Start | 启动 |
| Stop | 停止 |
| Delete | 删除 |
| Running | 运行中 |
| Stopped | 已停止 |
| Error | 错误 |
| Pipeline | 流水线 |
| AI Assistant | AI 助手 |
| Manual Editor | 手动编辑器 |
| Settings | 设置 |
| Import | 导入 |
| Export | 导出 |
| Recording | 录制 |
| Streaming | 推流 |
| HDMI Input | HDMI 输入 |
| Storage | 存储 |
| Bitrate | 码率 |
| Resolution | 分辨率 |

### Messages

| English | 简体中文 |
|---------|---------|
| Pipeline started successfully | 流水线启动成功 |
| Pipeline stopped | 流水线已停止 |
| Failed to start pipeline | 流水线启动失败 |
| HDMI signal detected | 检测到 HDMI 信号 |
| HDMI signal lost | HDMI 信号丢失 |
| Recording started | 开始录制 |
| Recording stopped | 录制已停止 |
| Storage full | 存储空间已满 |
| Device not found | 设备未找到 |
| AI is generating pipeline... | AI 正在生成流水线... |
| Enter your request | 输入您的需求 |

### AI Assistant

| English | 简体中文 |
|---------|---------|
| I'm a specialized GStreamer pipeline assistant. | 我是专业的 GStreamer 流水线助手。 |
| Describe what you want to stream | 描述您想要推流的内容 |
| Generating pipeline... | 正在生成流水线... |
| Pipeline generated | 流水线生成完成 |
| Error analyzing... | 正在分析错误... |

---

## Language Detection

Priority order:
1. User preference in settings
2. Browser language (`navigator.language`)
3. System locale
4. Fallback to English

```javascript
function getPreferredLanguage() {
    // Check saved preference
    const saved = localStorage.getItem('gst-manager-lang');
    if (saved) return saved;
    
    // Check browser
    const browserLang = navigator.language.split('-')[0];
    if (['en', 'zh'].includes(browserLang)) {
        return browserLang === 'zh' ? 'zh_CN' : 'en';
    }
    
    return 'en';
}
```

---

## Adding New Languages

1. Add language code to `po/LINGUAS`
2. Create `po/<lang>.po` from template
3. Translate strings
4. Rebuild frontend

---

## Notes

- AI responses are in the language of user's prompt (model-dependent)
- Error messages from GStreamer are English-only (system level)
- Date/time formats follow locale conventions
