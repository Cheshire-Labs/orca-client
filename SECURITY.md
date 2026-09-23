# Security

**This release is meant for internal use only.** Run it on a lab machine or
an internal network that you control, operated by people you trust.

Report a vulnerability to support@cheshirelabs.io. Please do not open a public
issue for one.

The agent executes the commands its runtime sends it. It does not restrict which
driver methods the runtime may call, so whatever you point `url` at has full
command authority over every instrument in your config, raw vendor console
commands included. Point it only at a runtime you control.

The Orca daemon performs no authentication. It listens on 127.0.0.1 only, so run
the agent on the same computer and treat that computer as the trust boundary.
