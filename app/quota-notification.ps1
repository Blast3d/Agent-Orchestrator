param([string]$Title, [string]$Message)
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$toastXml = New-Object Windows.Data.Xml.Dom.XmlDocument
$escapedTitle = [System.Security.SecurityElement]::Escape($Title)
$escapedMessage = [System.Security.SecurityElement]::Escape($Message)
$toastXml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$escapedTitle</text><text>$escapedMessage</text></binding></visual><audio silent='true'/></toast>")
$toastNotice = [Windows.UI.Notifications.ToastNotification]::new($toastXml)
$toastNotice.ExpirationTime = [DateTimeOffset]::Now.AddMinutes(5)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell').Show($toastNotice)
