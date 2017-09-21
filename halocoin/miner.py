import Queue
import multiprocessing
import random
import time
from multiprocessing import Process

import blockchain
import custom
import tools
from ntwrk import Response
from service import Service, threaded


class MinerService(Service):
    """
    Simple miner service. Starts running when miner is turned on.
    Executes number of workers as specified in config.
    Workers are run as different processes. Supports multicore mining.
    """
    def __init__(self, engine):
        Service.__init__(self, "miner")
        self.engine = engine
        self.db = None
        self.blockchain = None
        self.wallet = None
        self.core_count = multiprocessing.cpu_count() if custom.miner_core_count == -1 else custom.miner_core_count
        self.pool = []
        self.queue = multiprocessing.Queue()

    def set_wallet(self, wallet):
        self.wallet = wallet

    def on_register(self):
        self.db = self.engine.db
        self.blockchain = self.engine.blockchain

        if self.wallet is not None and 'pubkey' in self.wallet:
            return True
        else:
            return False

    def on_close(self):
        self.wallet = None
        self.close_workers()

    @threaded
    def worker(self):
        if self.blockchain.get_chain_state() == blockchain.BlockchainService.SYNCING:
            time.sleep(0.1)
            return

        candidate_block = self.get_candidate_block()
        tx_pool = self.blockchain.tx_pool()
        self.start_workers(candidate_block)

        possible_block = None
        while not MinerService.is_everyone_dead(self.pool) and self.threaded_running():
            if self.db.get('length')+1 != candidate_block['length'] or self.blockchain.tx_pool() != tx_pool:
                candidate_block = self.get_candidate_block()
                tx_pool = self.blockchain.tx_pool()
                self.start_workers(candidate_block)
            try:
                possible_block = self.queue.get(timeout=0.5)
                break
            except Queue.Empty:
                pass

        if possible_block is not None:
            tools.log('Mined block')
            tools.log(possible_block)
            self.blockchain.blocks_queue.put(possible_block)

    def start_workers(self, candidate_block):
        self.close_workers()
        for i in range(self.core_count):
            p = Process(target=MinerService.target, args=[candidate_block, self.queue])
            p.start()
            self.pool.append(p)

    def close_workers(self):
        for p in self.pool:
            p.terminate()
            p.join()
        self.pool = []

    def make_block(self, prev_block, txs, pubkey):
        leng = int(prev_block['length']) + 1
        target_ = self.blockchain.target(leng)
        diffLength = tools.hex_sum(prev_block['diffLength'], tools.hex_invert(target_))
        out = {'version': custom.version,
               'txs': txs + [self.make_mint(pubkey)],
               'length': leng,
               'time': time.time(),
               'diffLength': diffLength,
               'target': target_,
               'prevHash': tools.det_hash(prev_block)}
        return out

    def make_mint(self, pubkey):
        return {'type': 'mint',
                'pubkeys': [pubkey],
                'signatures': ['first_sig'],
                'count': 0}

    def genesis(self, pubkey):
        target_ = self.blockchain.target(0)
        out = {'version': custom.version,
               'length': 0,
               'time': time.time(),
               'target': target_,
               'diffLength': tools.hex_invert(target_),
               'txs': [self.make_mint(pubkey)]}
        return out

    def proof_of_work(self, block):
        if 'nonce' in block:
            block.pop('nonce')
        halfHash = tools.det_hash(block)
        block['nonce'] = random.randint(0, 10000000000000000000000000000000000000000)
        count = 0
        current_hash = tools.det_hash({'nonce': block['nonce'], 'halfHash': halfHash})
        while current_hash > block['target'] and self.threaded_running() and count < 100000:
            count += 1
            block['nonce'] += 1
            current_hash = tools.det_hash({'nonce': block['nonce'], 'halfHash': halfHash})

        if current_hash <= block['target']:
            return Response(True, block)
        else:
            return Response(False, None)

    def get_candidate_block(self):
        length = self.db.get('length')
        print 'Miner working for block', (length + 1)
        if length == -1:
            candidate_block = self.genesis(self.db.get('pubkey'))
        else:
            prev_block = self.db.get(length)
            candidate_block = self.make_block(prev_block, self.blockchain.tx_pool(), self.wallet['pubkey'])
        return candidate_block

    @staticmethod
    def target(candidate_block, queue):
        # Miner registered but no work is sent yet.
        if candidate_block is None:
            return
        if 'nonce' in candidate_block:
            candidate_block.pop('nonce')
        halfHash = tools.det_hash(candidate_block)
        candidate_block['nonce'] = random.randint(0, 10000000000000000000000000000000000000000)
        current_hash = tools.det_hash({'nonce': candidate_block['nonce'], 'halfHash': halfHash})
        while current_hash > candidate_block['target']:
            candidate_block['nonce'] += 1
            current_hash = tools.det_hash({'nonce': candidate_block['nonce'], 'halfHash': halfHash})
        if current_hash <= candidate_block['target']:
            queue.put(candidate_block)

    @staticmethod
    def is_everyone_dead(processes):
        for p in processes:
            if p.is_alive():
                return False
        return True
