# Side audit - 2026-08-01

Cutoff `2026-08-01T04:00:00+00:00`. Frames audited: **929**.

This compares **our summary-screen read** against **our draft-screen read**, on **one machine**. It cannot judge another contributor's rows, and a frame that could not be read is evidence about the frame reader, not about the summary reader - which is why `unreadable` is kept separate from `partial`.

## Verdicts

| verdict | n | share of audited |
|---|---:|---:|
| agree | 846 | 91.1% |
| mirrored | 76 | 8.2% |
| partial | 4 | 0.4% |
| unreadable | 2 | 0.2% |
| incomplete | 1 | 0.1% |

**Mirrored rate on the 922 adjudicated rows (agree + mirrored): 8.24% [6.64-10.20]**.

A rate near 50% would mean the frame reader is broken - not that half the corpus is mirrored - because a reader that assigns sides at random produces exactly that.

## What the frame reader actually returned

`classify` needs THREE on each side. This table is the reason the verdict table looks the way it does.

| blue read | red read | frames |
|---:|---:|---:|
| 3 | 2 | 927 |
| 2 | 2 | 1 |
| 1 | 2 | 1 |

The missing cell is plate 6 - the LAST pick of the snake draft - which has not locked at the moment the frame is saved. It is a capture-timing defect, not a geometry or threshold one: the five cells that DO read come back at 0.94-0.98 while plate 6 sits at ~0.68 with a margin of ~0.002, which is what an empty plate looks like.

## The one-sided check (diagnostic, NOT a verdict)

The blue trio comes back complete even when plate 6 does not, and a complete blue trio matching one stored trio exactly - with the short red read contained in the other - already fixes the orientation. The missing hero cannot change which way round the two trios sit.

This is the weaker rule, kept separate on purpose. `classify` remains the rule of record and the repair acts on IT, so nothing below has flipped, or can flip, a row.

| one-sided verdict | n |
|---|---:|
| agree | 846 |
| mirrored | 76 |
| partial | 4 |
| unreadable | 2 |
| incomplete | 1 |

**One-sided mirrored rate on 922 rows: 8.24% [6.64-10.20]**.

## Is mirroring associated with anything?

Every 2x2 below is computed on the ONE-SIDED adjudicated rows, because the rule of record adjudicated none.

**Stored outcome**

| group | n | mirrored | rate | 95% Wilson |
|---|---:|---:|---:|---|
| outcome = left | 529 | 45 | 8.5% | 6.4-11.2% |
| not outcome = left | 393 | 31 | 7.9% | 5.6-11.0% |

Two-proportion z = 0.338, p = 0.7356.

**Hour of day (UTC)**

| group | n | mirrored | rate | 95% Wilson |
|---|---:|---:|---:|---|
| captured at or after 12:00 | 496 | 45 | 9.1% | 6.8-11.9% |
| not captured at or after 12:00 | 426 | 31 | 7.3% | 5.2-10.1% |

Two-proportion z = 0.988, p = 0.3230.

**Rating order**

| group | n | mirrored | rate | 95% Wilson |
|---|---:|---:|---:|---|
| left_rating > right_rating | 449 | 1 | 0.2% | 0.0-1.3% |
| not left_rating > right_rating | 473 | 75 | 15.9% | 12.8-19.4% |

Two-proportion z = -8.627, p = 0.0000.

**Has a same-comps twin**

| group | n | mirrored | rate | 95% Wilson |
|---|---:|---:|---:|---|
| has a twin | 53 | 10 | 18.9% | 10.6-31.4% |
| not has a twin | 869 | 66 | 7.6% | 6.0-9.5% |

Two-proportion z = 2.897, p = 0.0038.

## P1a: the accuracy correction

`predicted_left` is draft-relative, so on a mirrored row the prediction is already on the correct side and it is the stored `outcome` that is flipped. Scored accuracy is therefore **understated** while mirrored rows exist. Computed on the one-sided classification, for the same reason as above.

| scoring | n | hits | accuracy |
|---|---:|---:|---:|
| as stored | 917 | 513 | 55.94% |
| with mirrored outcomes flipped | 917 | 531 | 57.91% |

## Every non-agreeing row (one-sided)

83 rows.

| match | verdict | captured | outcome | row left | row right | frame blue | frame red |
|---:|---|---|---|---|---|---|---|
| 625 | incomplete | 2026-07-30T05:16:02+00:00 | left | tasi, thador, zandrok | callan, korin | tasi, thador, zandrok | korin, temesia |
| 367 | mirrored | 2026-07-29T00:23:41+00:00 | right | hewynn, lily_may, thoran | hepler, korin, reinier | hepler, korin, reinier | lily_may, thoran |
| 376 | mirrored | 2026-07-29T02:15:47+00:00 | right | aliceth, dionel, tilaya | baelran, hugin, reinier | baelran, hugin, reinier | aliceth, dionel |
| 383 | mirrored | 2026-07-29T02:38:54+00:00 | right | kazim, lucca, thador | callan, cassadee, harak | callan, cassadee, harak | kazim, thador |
| 404 | mirrored | 2026-07-29T03:46:24+00:00 | left | berial, hodgkin, phraesto | alsa, callan, nazrik | alsa, callan, nazrik | hodgkin, phraesto |
| 436 | mirrored | 2026-07-29T14:29:22+00:00 | right | phraesto, pippa, zorya | damian, kazim, velara | damian, kazim, velara | phraesto, zorya |
| 437 | mirrored | 2026-07-29T14:31:59+00:00 | left | lyca, seth, zandrok | silven, thador, valka | silven, thador, valka | lyca, zandrok |
| 438 | mirrored | 2026-07-29T14:36:03+00:00 | right | daimon, lucca, lyca | alsa, rowan, tilaya | alsa, rowan, tilaya | daimon, lyca |
| 457 | mirrored | 2026-07-29T15:31:02+00:00 | left | aurora, gwyneth, zandrok | evie, hepler, zorya | evie, hepler, zorya | aurora, gwyneth |
| 481 | mirrored | 2026-07-29T16:40:43+00:00 | right | lucius, rhys, seth | lorsan, thador, walker | lorsan, thador, walker | lucius, rhys |
| 502 | mirrored | 2026-07-29T20:54:46+00:00 | left | harak, silven, sonja | aliceth, hepler, solise | aliceth, hepler, solise | harak, sonja |
| 724 | mirrored | 2026-07-30T11:01:46+00:00 | left | daimon, eironn, gwyneth | parisa, seth, temesia | parisa, seth, temesia | daimon, gwyneth |
| 810 | mirrored | 2026-07-30T18:52:27+00:00 | right | galahad, kordan, ludovic | alsa, ulmus, velara | alsa, ulmus, velara | galahad, ludovic |
| 819 | mirrored | 2026-07-30T20:04:32+00:00 | left | cyran, thador, valka | lily_may, pippa, zandrok | lily_may, pippa, zandrok | cyran, thador |
| 826 | mirrored | 2026-07-30T20:27:24+00:00 | right | harak, lorsan, lumont | hugin, mirael, valka | hugin, mirael, valka | harak, lorsan |
| 834 | mirrored | 2026-07-30T20:48:17+00:00 | right | lucy, lyca, thador | hepler, valka, velara | hepler, valka, velara | lucy, thador |
| 840 | mirrored | 2026-07-30T21:02:55+00:00 | left | lyca, silven, valka | gerda, harak, parisa | gerda, harak, parisa | silven, valka |
| 841 | mirrored | 2026-07-30T21:05:56+00:00 | right | hugin, odie, sonja | aurora, igor, talene | aurora, igor, talene | hugin, sonja |
| 844 | mirrored | 2026-07-30T21:13:06+00:00 | right | antandra, faramor, kordan | hugin, marilee, phraesto | hugin, marilee, phraesto | faramor, kordan |
| 863 | mirrored | 2026-07-30T22:10:54+00:00 | left | hepler, mikola, nazrik | aliceth, brutus, hodgkin | aliceth, brutus, hodgkin | hepler, nazrik |
| 872 | mirrored | 2026-07-30T22:36:05+00:00 | left | damian, phraesto, satrana | daimon, pippa, zorya | daimon, pippa, zorya | damian, phraesto |
| 882 | mirrored | 2026-07-30T23:01:35+00:00 | right | kruger, ludovic, talene | aliceth, silven, thador | aliceth, silven, thador | kruger, talene |
| 884 | mirrored | 2026-07-30T23:08:12+00:00 | left | hepler, lorsan, tasi | galahad, granny_dahnie, rhys | galahad, granny_dahnie, rhys | hepler, lorsan |
| 885 | mirrored | 2026-07-30T23:10:49+00:00 | right | eironn, satrana, temesia | granny_dahnie, phraesto, talene | granny_dahnie, phraesto, talene | satrana, temesia |
| 904 | mirrored | 2026-07-31T00:04:24+00:00 | right | carolina, gerda, reinier | niru, phraesto, scarlita | niru, phraesto, scarlita | carolina, reinier |
| 912 | mirrored | 2026-07-31T00:29:16+00:00 | left | aliceth, carolina, ulmus | nara, solise, thador | nara, solise, thador | aliceth, carolina |
| 926 | mirrored | 2026-07-31T02:49:09+00:00 | right | hepler, hugin, odie | aliceth, solise, valka | aliceth, solise, valka | hepler, odie |
| 927 | mirrored | 2026-07-31T02:52:30+00:00 | left | berial, natsu, temesia | hodgkin, lily_may, zandrok | hodgkin, lily_may, zandrok | berial, natsu |
| 941 | mirrored | 2026-07-31T03:34:37+00:00 | left | igor, kazim, lorsan | lumont, silven, velara | lumont, silven, velara | igor, lorsan |
| 943 | mirrored | 2026-07-31T03:39:14+00:00 | left | cryonaia, dunlingr, thador | aliceth, lucca, pippa | aliceth, lucca, pippa | cryonaia, dunlingr |
| 948 | mirrored | 2026-07-31T04:28:02+00:00 | right | gwyneth, lumont, silven | berial, talene, tilaya | berial, talene, tilaya | gwyneth, silven |
| 950 | mirrored | 2026-07-31T05:01:32+00:00 | left | hepler, thador, valka | isabella, kordan, sonja | isabella, kordan, sonja | hepler, valka |
| 991 | mirrored | 2026-07-31T06:25:54+00:00 | right | lyca, temesia, thador | hepler, odie, solise | hepler, odie, solise | lyca, temesia |
| 1036 | mirrored | 2026-07-31T08:00:28+00:00 | left | evie, galahad, thoran | lily_may, phraesto, rowan | lily_may, phraesto, rowan | evie, galahad |
| 1043 | mirrored | 2026-07-31T08:12:39+00:00 | left | lorsan, sonja, valka | hepler, silven, thador | hepler, silven, thador | sonja, valka |
| 1063 | mirrored | 2026-07-31T08:54:37+00:00 | left | lorsan, perseus, sonja | kordan, phraesto, rhys | kordan, phraesto, rhys | lorsan, sonja |
| 1088 | mirrored | 2026-07-31T09:40:15+00:00 | left | lorsan, pippa, thoran | hodgkin, parisa, perseus | hodgkin, parisa, perseus | lorsan, pippa |
| 1097 | mirrored | 2026-07-31T09:53:54+00:00 | right | phraesto, solise, temesia | faramor, tilaya, zandrok | faramor, tilaya, zandrok | phraesto, temesia |
| 1104 | mirrored | 2026-07-31T10:08:28+00:00 | left | arden, carolina, dunlingr | berial, hodgkin, pang | berial, hodgkin, pang | arden, dunlingr |
| 1108 | mirrored | 2026-07-31T10:13:40+00:00 | left | phraesto, pippa, scarlita | hugin, indris, valka | hugin, indris, valka | phraesto, pippa |
| 1111 | mirrored | 2026-07-31T10:20:51+00:00 | left | antandra, daimon, dionel | callan, nara, pippa | callan, nara, pippa | daimon, dionel |
| 1117 | mirrored | 2026-07-31T10:30:16+00:00 | left | baelran, parisa, phraesto | nara, pippa, temesia | nara, pippa, temesia | parisa, phraesto |
| 1132 | mirrored | 2026-07-31T11:03:41+00:00 | right | gerda, gwyneth, kruger | aliceth, callan, damian | aliceth, callan, damian | gwyneth, kruger |
| 1133 | mirrored | 2026-07-31T11:06:34+00:00 | left | dunlingr, parisa, valka | callan, nazrik, silven | callan, nazrik, silven | dunlingr, valka |
| 1143 | mirrored | 2026-07-31T11:29:50+00:00 | left | florabelle, galahad, lumont | aurora, daimon, lily_may | aurora, daimon, lily_may | florabelle, galahad |
| 1151 | mirrored | 2026-07-31T11:55:11+00:00 | left | florabelle, phraesto, tasi | galahad, harak, hodgkin | galahad, harak, hodgkin | florabelle, phraesto |
| 1153 | mirrored | 2026-07-31T11:59:59+00:00 | right | antandra, mikola, perseus | granny_dahnie, hodgkin, pippa | granny_dahnie, hodgkin, pippa | mikola, perseus |
| 1154 | mirrored | 2026-07-31T12:02:37+00:00 | left | lucy, marilee, thador | daimon, eironn, faramor | daimon, eironn, faramor | lucy, thador |
| 1155 | mirrored | 2026-07-31T12:06:02+00:00 | left | berial, sonja, temesia | kruger, natsu, zandrok | kruger, natsu, zandrok | berial, sonja |
| 1165 | mirrored | 2026-07-31T12:34:43+00:00 | right | alsa, lorsan, phraesto | hugin, laios, odie | hugin, laios, odie | lorsan, phraesto |
| 1177 | mirrored | 2026-07-31T13:14:35+00:00 | left | alsa, dunlingr, sonja | hepler, satrana, shadewing | hepler, satrana, shadewing | dunlingr, sonja |
| 1182 | mirrored | 2026-07-31T13:29:46+00:00 | right | daimon, lucy, pippa | callan, hugin, odie | callan, hugin, odie | daimon, pippa |
| 1184 | mirrored | 2026-07-31T13:34:56+00:00 | left | granny_dahnie, indris, sonja | eironn, hugin, parisa | eironn, hugin, parisa | indris, sonja |
| 1201 | mirrored | 2026-07-31T14:27:48+00:00 | left | koko, lily_may, talene | gerda, lucca, perseus | gerda, lucca, perseus | lily_may, talene |
| 1217 | mirrored | 2026-07-31T15:20:07+00:00 | left | fay, harak, tasi | aurora, eironn, ludovic | aurora, eironn, ludovic | harak, tasi |
| 1218 | mirrored | 2026-07-31T15:22:48+00:00 | right | lily_may, sonja, tasi | galahad, indris, korin | galahad, indris, korin | lily_may, sonja |
| 1227 | mirrored | 2026-07-31T15:50:43+00:00 | left | bryon, carolina, dunlingr | arden, callan, parisa | arden, callan, parisa | carolina, dunlingr |
| 1236 | mirrored | 2026-07-31T16:17:21+00:00 | left | cassadee, silven, sonja | alsa, callan, zorya | alsa, callan, zorya | cassadee, silven |
| 1240 | mirrored | 2026-07-31T16:27:23+00:00 | left | daimon, hugin, solise | kordan, nara, silven | kordan, nara, silven | daimon, hugin |
| 1246 | mirrored | 2026-07-31T16:43:24+00:00 | left | aurora, igor, lucy | nara, solise, temesia | nara, solise, temesia | aurora, lucy |
| 1304 | mirrored | 2026-07-31T19:20:53+00:00 | right | hepler, lily_may, rhys | aurora, gerda, odie | aurora, gerda, odie | hepler, rhys |
| 1313 | mirrored | 2026-07-31T19:42:35+00:00 | left | florabelle, solise, sonja | brutus, daimon, rhys | brutus, daimon, rhys | solise, sonja |
| 1316 | mirrored | 2026-07-31T19:47:17+00:00 | left | galahad, silven, tilaya | aurora, kafra, lily_may | aurora, kafra, lily_may | galahad, tilaya |
| 1318 | mirrored | 2026-07-31T19:49:52+00:00 | right | dionel, lily_may, sonja | berial, natsu, soren | berial, natsu, soren | dionel, sonja |
| 1324 | mirrored | 2026-07-31T20:02:58+00:00 | left | solise, sonja, viperian | aurora, florabelle, hugin | aurora, florabelle, hugin | solise, sonja |
| 1357 | mirrored | 2026-07-31T21:13:31+00:00 | right | alsa, arden, thador | aurora, hodgkin, solise | aurora, hodgkin, solise | alsa, thador |
| 1364 | mirrored | 2026-07-31T21:34:27+00:00 | right | alsa, korin, phraesto | daimon, pippa, vala | daimon, pippa, vala | alsa, phraesto |
| 1365 | mirrored | 2026-07-31T21:38:10+00:00 | right | sonja, tilaya, valka | reinier, solise, temesia | reinier, solise, temesia | tilaya, valka |
| 1367 | mirrored | 2026-07-31T21:44:31+00:00 | left | indris, kazim, tilaya | eironn, gwyneth, hugin | eironn, gwyneth, hugin | kazim, tilaya |
| 1373 | mirrored | 2026-07-31T22:05:41+00:00 | right | odie, sonja, vala | dunlingr, rhys, seth | dunlingr, rhys, seth | odie, vala |
| 1391 | mirrored | 2026-07-31T22:59:01+00:00 | left | bonnie, igor, talene | aurora, kruger, lenya | aurora, kruger, lenya | bonnie, talene |
| 1406 | mirrored | 2026-07-31T23:44:20+00:00 | left | damian, talene, tasi | daimon, faramor, valka | daimon, faramor, valka | damian, talene |
| 1408 | mirrored | 2026-07-31T23:48:53+00:00 | right | cyran, lucca, pippa | eironn, lily_may, ulmus | eironn, lily_may, ulmus | cyran, pippa |
| 1412 | mirrored | 2026-07-31T23:58:43+00:00 | left | bryon, dionel, lorsan | harak, koko, thoran | harak, koko, thoran | dionel, lorsan |
| 1472 | mirrored | 2026-08-01T03:10:07+00:00 | left | galahad, hepler, pippa | brutus, eironn, nazrik | brutus, eironn, nazrik | galahad, pippa |
| 1474 | mirrored | 2026-08-01T03:15:55+00:00 | left | alsa, evie, kordan | hugin, phraesto, temesia | hugin, phraesto, temesia | alsa, evie |
| 1476 | mirrored | 2026-08-01T03:21:34+00:00 | right | cyran, evie, lorsan | berial, callan, silven | berial, callan, silven | evie, lorsan |
| 517 | partial | 2026-07-29T21:52:16Z | right | damian, kordan, pippa | galahad, ludovic, thador | dunlingr, rhys, solise | daimon, silven |
| 519 | partial | 2026-07-29T21:54:26Z | left | aliceth, harak, igor | kafra, kruger, sonja | aliceth, hugin, kruger | antandra, dionel |
| 522 | partial | 2026-07-29T21:58:02Z | right | aurora, scarlita, valka | parisa, reinier, ulmus | aliceth, callan, galahad | dionel, phraesto |
| 524 | partial | 2026-07-29T22:00:12Z | left | indris, lorsan, temesia | nara, thador, valka | parisa, phraesto, temesia | bonnie, daimon |
| 705 | unreadable | 2026-07-30T09:06:55+00:00 | right | atalanta, kafra, reinier | hugin, kazim, lumont | kafra, reinier | hugin, kazim |
| 1121 | unreadable | 2026-07-31T10:34:59+00:00 | left | alsa, kazim, pang | hodgkin, pippa, temesia | kazim | pippa, temesia |

