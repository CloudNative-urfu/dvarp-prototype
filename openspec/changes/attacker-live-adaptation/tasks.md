## 1. Tasks

- [x] perception Recognise short refund confirmations and refund ids as accepted, keep negated sentences out
- [x] repeat-in-attempt Note after a repeated message and label `repeated_message`
- [x] mutants Restore the target line of the broken LimitExceeded mutant; 21/21 killed
- [x] new-opening Add the different-opening rule to the mode A prompt after a failed attempt; label repeated openers
- [x] check Attacker unit tests and mutant targets unchanged; ruff clean
- [x] live Live mode A run on the local model: g0 stops after the first executed attempt; g_in attempts after refusal open differently
