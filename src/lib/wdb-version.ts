/**
 * Which build of the WebDevBar fork this is.
 *
 * The upstream version says which AdbAutoPlayer release our patches sit on. It does not
 * say which of OUR builds is running, and those differ by a great deal within one
 * upstream version.
 *
 * This MUST match `WDB_RELEASE` in
 * `src-tauri/src-python/adb_auto_player/wdb_version.py`, which is the source of truth.
 * `build-rpm.sh` refuses to build if they disagree, so the drift cannot ship.
 */
export const WDB_RELEASE = "32";

export function wdbVersion(appVersion: string): string {
  return `${appVersion}-${WDB_RELEASE}`;
}
