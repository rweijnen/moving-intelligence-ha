# Brand assets

These icons are intended for submission to the
[home-assistant/brands](https://github.com/home-assistant/brands) repository.

Once accepted, Home Assistant will automatically display them in the integration
list and on the integration page. Custom integrations get their assets under
`custom_integrations/<domain>/`.

## How to submit

1. Fork https://github.com/home-assistant/brands
2. Copy these files into the fork at:
   - `custom_integrations/mi_home/icon.png` (256×256)
   - `custom_integrations/mi_home/icon@2x.png` (512×512)
3. Open a PR. The CI will validate the assets.

After merge, brand assets are served from `https://brands.home-assistant.io/`
within ~24 hours.

## Source

The icon is the official Moving Intelligence app icon, extracted from the
public Android APK. Used here in good faith to identify the brand the
integration connects to.
