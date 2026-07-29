"""Which build of the WebDevBar fork this is.

The upstream version (12.9.25) says which AdbAutoPlayer release our patches sit on. It
does NOT say which of OUR builds you are running, and those differ by a great deal - the
odds model, the overlay and the collection mode have all changed several times within one
upstream version.

So the fork carries its own release number, and this file is where it lives. The RPM and
the release tag READ it rather than the other way round: a number kept in packaging config
cannot be seen at runtime, which is exactly when somebody needs it - reading a log and
asking "is this the build with the fix?".

Bump this when cutting a fork build. `build-rpm.sh` picks it up automatically.
"""

WDB_RELEASE = "22"


def wdb_version(app_version: str) -> str:
    """The fork's full version, e.g. "12.9.25-19".

    Args:
        app_version: The upstream version this build is based on.

    Returns:
        The upstream version with the fork release appended.
    """
    return f"{app_version}-{WDB_RELEASE}"
