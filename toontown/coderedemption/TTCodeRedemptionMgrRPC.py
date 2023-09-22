from direct.showbase.DirectObject import DirectObject
from direct.directnotify.DirectNotifyGlobal import directNotify
from direct.task import Task

from panda3d.core import *

from otp.otpbase import PythonUtil
from otp.otpbase.PythonUtil import Functor, SerialNumGen, choice

if ConfigVariableBool('want-code-redemption-mysql', False).getValue():
    from toontown.coderedemption.TTCodeRedemptionDBMYSQL import TTCodeRedemptionDB
else:
    from toontown.coderedemption.TTCodeRedemptionDBJSON import TTCodeRedemptionDB
from toontown.coderedemption.TTCodeDict import TTCodeDict
from toontown.coderedemption import TTCodeRedemptionConsts
from toontown.rpc.AwardManagerUD import AwardManagerUD
from toontown.rpc import AwardManagerConsts

import datetime
import traceback
import json


class TTCodeRedemptionMgrRPC(DirectObject):
    notify = directNotify.newCategory('TTCodeRedemptionMgrRPC')

    class GenericErrors:
        EmptyInput = 'This field is required'
        InvalidNumber = 'Please enter a number'
        FieldsMustMatch = 'This field must match the previous field'

    class CreateErrors:
        InvalidCharInLotName = 'Name can only contain lowercase ASCII letters, numbers, and underscores'
        UsedLotName = 'Lot name is already in use'

    class CodeErrors:
        InvalidCharInCode = 'Code can only contain alphanumeric characters and dashes'
        InvalidCode = 'Invalid code'
        MustContainManualChar = ('Code must contain at least one of the following: %s' % TTCodeDict.ManualOnlyCharacters)
        CodeAlreadyExists = 'Code already exists'
        CodeTooLong = 'Code must be %s characters or less' % TTCodeRedemptionConsts.MaxCustomCodeLen

    class RedeemErrors:
        InvalidCharInAvId = 'AvId can only contain numbers'
        CodeIsExpired = 'Code is expired'
        CodeAlreadyRedeemed = 'Code has already been redeemed'
        AwardCouldntBeGiven = 'Award could not be given, code not processed'

    def __init__(self, air, db):
        self.air = air
        self.db = db

        self.createLotSerialGen = SerialNumGen()
        self.createLotId2task = {}

    def delete(self):
        for task in list(self.createLotId2task.values()):
            self.removeTask(task)

        self.createLotId2task = {}

    def createLotTask(self, task):
        for result in task.gen:
            break

        if result is True:
            self.handleRPCViewLot(task.request, task.lotName, True, self.db.LotFilter.All)
            del self.createLotId2task[task.createLotId]
            return Task.done

        return Task.cont

    def warnTryAgainLater(self, exception):
        # if we catch a TryAgainLater, drop this code submission on the floor. The AI
        # will resubmit the code shortly
        self.notify.warning('%s' % exception)
        self.notify.warning('caught TryAgainLater exception from TTCodeRedemptionDB. Dropping request')

    def handleRPCGetAwardChoices(self, request):
        assert self.notify.debugCall()

        try:
            awardChoices = AwardManagerUD.getAwardChoicesArray()
            allowAutoGenerated = ConfigVariableBool('want-unique-code-generation', False).getValue()
            maxCodeLength = TTCodeRedemptionConsts.MaxCustomCodeLen
            lotNames = self.db.getLotNames()

            return request.result({
                'awardChoices': awardChoices,
                'allowAutoGenerated': allowAutoGenerated,
                'maxCodeLength': maxCodeLength,
                'lotNames': lotNames
            })
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def handleRPCCheckForLots(self, request):
        assert self.notify.debugCall()

        try:
            lotNames = self.db.getLotNames()
            hasLots = len(lotNames) > 0

            return request.result({
                'hasLots': hasLots
            })
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def handleRPCGetLotNames(self, request):
        assert self.notify.debugCall()

        try:
            lotNames = self.db.getLotNames()

            return request.result({
                'lots': lotNames
            })
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def handleRPCGetLotNamesWithExpiration(self, request):
        assert self.notify.debugCall()

        try:
            lotNames = self.db.getExpirationLotNames()

            return request.result({
                'lots': lotNames
            })
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def handleRPCCreateLot(self, request, manualCode, numCodes, lotName, rewardType, rewardItemId, manualCodeStr, hasExpiration, expirationMonth, expirationDay, expirationYear):
        assert self.notify.debugCall()

        try:
            if manualCodeStr:
                if self.db.codeExists(manualCodeStr):
                    return request.error(9996, self.CodeErrors.CodeAlreadyExist)

            expirationDate = None

            if hasExpiration:
                try:
                    expirationDate = datetime.date(int(expirationYear), int(expirationMonth), int(expirationDay))
                except ValueError as e:
                    return request.error(9997, str(e).capitalize())

            if manualCode:
                self.db.createManualLot(lotName, manualCodeStr, rewardType, rewardItemId, expirationDate)

                self.handleRPCViewLot(request, lotName, True, self.db.LotFilter.All)
            else:
                createLotId = self.createLotSerialGen.next()
                gen = self.db.createLot(self.air.codeRedemptionManager._requestRandomSamples, lotName, numCodes,
                                        rewardType, rewardItemId,
                                        expirationDate)

                t = self.addTask(self.createLotTask, '%s-createLot-%s' % (self.__class__.__name__, createLotId))
                t.createLotId = createLotId
                t.gen = gen
                t.request = request
                t.lotName = lotName

                self.createLotId2task[createLotId] = t
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def handleRPCViewLot(self, request, lotName, justCode, filterOption, extraMessage=None):
        assert self.notify.debugCall()

        try:
            results = self.db.getCodesInLot(lotName, justCode, filterOption)
            manual = (lotName in self.db.getManualLotNames())
            message = ('Code Lot: %s%s, %s results' % (lotName, choice(filterOption == self.db.LotFilter.All, '', ' (%s)' % filterOption), len(results)))
            codeLotDetails = self.createCodeLotDetailsJSON(results, justCode, manual)

            return request.result({
                'message': message,
                'extraMessage': extraMessage,
                'codeLotDetails': json.dumps(codeLotDetails)
            })
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def handleRPCModifyLot(self, request, lotName, expirationMonth, expirationDay, expirationYear):
        assert self.notify.debugCall()

        try:
            exp = '%s-%02d-%02d' % (expirationYear, int(expirationMonth), int(expirationDay), )
            self.db.setExpiration(lotName, exp)

            message = 'Expiration date set to %s' % (exp, )

            self.handleRPCViewLot(request, lotName, False, self.db.LotFilter.All, message)
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def handleRPCDeleteLot(self, request, lotName):
        assert self.notify.debugCall()

        try:
            success = False
            preLotNames = self.db.getLotNames()
            if lotName in preLotNames:
                self.db.deleteLot(lotName)
                postLotNames = self.db.getLotNames()
                if lotName not in postLotNames:
                    success = True

            message = choice(success, 'Code Lot %s deleted' % (lotName, ), 'Could not delete lot %s' % (lotName, ))

            return request.result({
                'message': message,
            })
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def handleRPCLookup(self, request, code=None, avId=None):
        assert self.notify.debugCall()

        try:
            if avId is not None:
                codes = self.db.lookupCodesRedeemedByAvId(avId)
            else:
                codes = [code, ]

            codeFields = []

            for cd in codes:
                codeFields.append(self.db.getCodeDetails(cd))

            if avId is not None:
                queryType = 'avId=%s' % avId
            else:
                queryType = 'code=%s' % code

            message = ('Code Lookup: %s, %s results' % (queryType, len(codeFields)))
            codeLotDetails = self.createCodeLotDetailsJSON(codeFields)

            return request.result({
                'message': message,
                'codeLotDetails': json.dumps(codeLotDetails)
            })
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())

    def createCodeLotDetailsJSON(self, fieldRows, justCode=False, manual=False):
        internalFields = ('name', 'lot.lot_id', 'lot_id', 'size',
                          'reward.reward_id', 'reward_id',)

        nameTransform = {
            'av_id': 'redeemedAvId',
            TTCodeRedemptionDB.RewardTypeFieldName: 'rewardCategory',
            TTCodeRedemptionDB.RewardItemIdFieldName: 'rewardItem',
        }

        transAndField = []

        if justCode:
            transAndField.append(['code', 'code'])
        else:
            fieldSet = set()

            for row in fieldRows:
                for field in row:
                    if field not in fieldSet:
                        if field not in internalFields:
                            transAndField.append([nameTransform.get(field, field), field])
                            fieldSet.add(field)

        # sort by transformed name, keep track of original field name
        transAndField.sort()

        jsonData = []

        for row in fieldRows:
            json = {}

            for trans, field in transAndField:
                if justCode:
                    value = row
                else:
                    value = row[field]

                if field == 'code':
                    # if the code is manually-entered, don't modify it to make it readable
                    # if the code row has a manual field, go by that,
                    # otherwise use the keyword arg to this method
                    if 'manual' in row:
                        isManual = row['manual'] == 'T'
                    else:
                        isManual = manual
                    if not isManual:
                        value = TTCodeDict.getReadableCode(value)
                else:
                    if trans == 'rewardCategory':
                        value = AwardManagerUD.getAwardTypeName(row[field])
                    if trans == 'rewardItem':
                        typeId = int(row[TTCodeRedemptionDB.RewardTypeFieldName])
                        itemId = int(row[field])
                        value = AwardManagerUD.getAwardText(typeId, itemId)
                    if field in ('manual', 'redeemed'):
                        value = {'T': 'Yes',
                                 'F': 'No',
                                 }[row[field]]
                    value = str(value)

                json[trans] = value

            if json != {}:
                jsonData.append(json)

        return jsonData

    def handleRPCRedeemCode(self, request, code, avId):
        assert self.notify.debugCall()

        try:
            avId = int(avId)
            code = str(code)

            result = self.db.redeemCode(code, avId, self.air.codeRedemptionManager, Functor(self.handleRPCRedeemResult, request, code, avId))

            if result is not None:
                error = {
                    TTCodeRedemptionConsts.RedeemErrors.CodeDoesntExist: self.CodeErrors.InvalidCode,
                    TTCodeRedemptionConsts.RedeemErrors.CodeIsExpired: self.RedeemErrors.CodeIsExpired,
                    TTCodeRedemptionConsts.RedeemErrors.CodeAlreadyRedeemed: self.RedeemErrors.CodeAlreadyRedeemed,
                    TTCodeRedemptionConsts.RedeemErrors.AwardCouldntBeGiven: self.RedeemErrors.AwardCouldntBeGiven,
                }[result]

                return request.error(9998, error)
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()

    def handleRPCRedeemResult(self, request, code, avId, result, awardMgrResult):
        assert self.notify.debugCall()

        try:
            errMap = {
                TTCodeRedemptionConsts.RedeemErrors.CodeDoesntExist: self.CodeErrors.InvalidCode,
                TTCodeRedemptionConsts.RedeemErrors.CodeIsExpired: self.RedeemErrors.CodeIsExpired,
                TTCodeRedemptionConsts.RedeemErrors.CodeAlreadyRedeemed: self.RedeemErrors.CodeAlreadyRedeemed,
                TTCodeRedemptionConsts.RedeemErrors.AwardCouldntBeGiven: self.RedeemErrors.AwardCouldntBeGiven,
            }

            if result in (errMap):
                errStr = errMap[result]
                if result == TTCodeRedemptionConsts.RedeemErrors.AwardCouldntBeGiven:
                    errStr += ': %s' % AwardManagerConsts.GiveAwardErrors.getString(awardMgrResult)

                return request.error(9997, errStr)
            else:
                rewardType, rewardId = self.db.getRewardFromCode(code)
                text = ('Redeemed code %s for avId %s, awarded [%s | %s].' % (
                    code, avId,
                    AwardManagerUD.getAwardTypeName(rewardType),
                    AwardManagerUD.getAwardText(rewardType, rewardId)))

                return request.result({
                    'results': text
                })
        except TTCodeRedemptionDB.TryAgainLater as e:
            self.warnTryAgainLater(e)
            return request.error(9999, 'Unavailable')
        except:
            traceback.print_exc()
            return request.error(9999, PythonUtil.describeException())