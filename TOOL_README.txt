=====================================================================
 FINESIII RPA v3 - TEST TOOL: CLEAR THE TICKET DATA
 For starting a new round of testing with an empty request list
=====================================================================
 One new file. No file of the application is changed, and the
 application does not use this file: it runs only when started by
 hand.

     app\scripts\clear_ticket_data.py

 Copy the CONTENT of the "app" folder over the application folder
 (C:\FINESIII\app), keeping the folders. Run the commands in PowerShell
 from the application folder, signed in as the bot account (the
 database password is kept in the credential store of that account).

 WHAT IT DOES

 It finds the database the application is connected to from the
 application's own settings, SQLite or PostgreSQL, and removes the
 requests of the two ticket tables:

     request_queue          the Requests list
     transaction_history    the History list

 KEPT     Completed requests, exactly as they are, with their whole
          history (an earlier failed attempt of a request that later
          completed stays as well).
 REMOVED  Queued, In progress, Failed, Cancelled, Needs verification
          and Already removed requests, and their history.

 A removed ticket that is still open in ServiceNow is picked up again
 by the next poll, as a new ticket. A completed ticket stays known to
 the bot and is not run a second time.

 NOT TOUCHED
   Console accounts, roles and settings; credentials; the audit trail;
   evidence folders (screenshots, printouts); logs; ServiceNow itself.

 STEPS

   1. Stop the service
        cd C:\FINESIII\app
        .\.venv\Scripts\python.exe desktop\app.py --stop

   2. Look first. Nothing is changed by this command; it lists the
      connected database and the rows per status, kept and removed.
        .\.venv\Scripts\python.exe scripts\clear_ticket_data.py

   3. Remove. The script asks for the word DELETE before it starts.
        .\.venv\Scripts\python.exe scripts\clear_ticket_data.py --apply

   4. Start the service
        .\.venv\Scripts\python.exe desktop\app.py --agent

 POSTGRESQL: "THIS LOGIN IS NOT ALLOWED TO DELETE"

   A database that was set up with an administrator address protects
   the history: the application login can add rows but cannot delete
   them. The script then stops at step 3 and changes nothing. Run it
   with the administrator address of the same server for this one run;
   the password is asked for and is not shown or stored:

        .\.venv\Scripts\python.exe scripts\clear_ticket_data.py --apply --admin-url 'postgresql://postgres@localhost:5432/postgres'

   The rights of the application login are not changed. The database
   that is cleared is always the one the application is connected to,
   whatever database name the administrator address carries.

 OPTIONS

   --keep completed,needs_verification,already_removed
        Keep more statuses than Completed. Names: completed,
        needs_verification, already_removed, failed, cancelled, queued,
        in_progress.
        Note: a request that Needs verification may already have been
        sent to FINESIII. When it is removed and its ticket is still
        open, the bot runs the ticket again. Keep that status when
        this must not happen.

   --reset-ticket-memory
        Also forget which ServiceNow tickets the bot already reported
        as refused (not for FINESIII, failed a check, duplicate). They
        are then reported again by the next poll. Without it a ticket
        that was refused before and has not changed stays silent.

   --no-backup          Do not write the backup file.
   --backup-dir PATH    Another folder for the backup file.
   --yes                Do not ask for the word DELETE.
   --force              Continue although a request is marked In
                        progress or the service looks running. Only
                        when the service is known to be stopped.

 SAFETY

   - Without --apply nothing is changed.
   - The script stops when the service is running or a request is In
     progress.
   - It stops when no settings file is found for the application
     folder, so it can never clear a database other than the connected
     one.
   - Before anything is removed, the rows are written to a backup file,
     one JSON object per line, passwords left out:
         backups\ticket_data_<date>_<time>.jsonl   (in the data folder)
   - Both tables are cleared in one transaction: everything or nothing.
   - Each run adds one line to logs\ticket_data_reset.log: when, which
     account, which database, how many rows, which backup file.

 EXIT CODE   0 done or nothing to do.  1 a problem.  2 stopped for
             safety. In every case other than "done", nothing was
             changed.

 TO REMOVE THE TOOL
   Delete C:\FINESIII\app\scripts\clear_ticket_data.py. Nothing else
   refers to it.
