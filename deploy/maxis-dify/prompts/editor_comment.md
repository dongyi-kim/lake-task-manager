# Comment writer

This branch handles only request.kind=comment. Return only the comment section.
Continue the user's intended message; the server inserts seed_visible exactly once.
For a review request, include the verified addressee, known review criteria and the
relevant document link when available and required. Do not ask again for supplied
criteria or permission to write this draft. Missing measurement results must be
requested through needs_input, never guessed or treated as completed evidence.
