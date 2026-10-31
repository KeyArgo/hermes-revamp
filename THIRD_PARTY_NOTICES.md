# Third-party notices

- **Webamp 2.3.1** is bundled in `web/vendor/` under the MIT License; the included license text is [web/vendor/WEBAMP-LICENSE.txt](web/vendor/WEBAMP-LICENSE.txt). It originated from the unmodified npm artifact `webamp@2.3.1`, file `package/built/webamp.butterchurn-bundle.min.mjs` (npm tarball integrity `sha512-u2NpSKYsUfDKltcvteF3vDbAzp+kDiymr5Sn0Vgfl+Uk9Kvd4GmuaF3stOxKNLbDH+RkJ2YZfus7O3W2axq4Qg==`; upstream file SHA-256 `679e18bd04b837e0fb420bf08a7d194d502b26c894a4e282d26a491bf7509105`).

  The vendored file has two documented downstream display corrections. Commit `57545bd0a3b6fab643379d7fda9d7e908f74fde2` keeps the main-window spectrum drawing while MilkDrop is open (it reads the display style rather than the MilkDrop-aware selector) and uses calmer peak/falloff settings; the 1.0 alpha 5 fix passes that selector the display state it expects (`zq(e=>Tf(e.display))`), without which the spectrum never drew, and lets its draw loop honour an optional frame budget (`window.__revampVisFps`, set only under software WebGL). No other bundle code was changed. The shipped file SHA-256 is `0c5f7fe0dc77d5c53fb56fdbf12282000632b8efc7564156d9bbfeaf4333be3e`. Preserve this provenance and update both hashes if the bundle changes.
- **Butterchurn** and its MilkDrop preset ecosystem are used through the bundled Webamp/Butterchurn build. Their source projects are MIT licensed; preserve their upstream notices when updating the bundle.
- **Radio Browser** supplies an optional public station directory. Its API/service terms and station metadata remain subject to Radio Browser and the station providers.
- **LRCLIB** supplies optional lyric lookup data. Lyrics and metadata remain subject to their respective rightsholders and LRCLIB’s service terms.
- **Winamp Skin Museum** supplies optional skin browsing and downloads. Individual skins remain subject to their authors’ rights and the museum’s terms.

REVamp is not affiliated with any of these projects or services.
