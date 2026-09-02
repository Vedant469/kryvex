// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract VerificationLog {
    struct Record {
        string faceHash;
        string matchedUrl;
        string postHash;
        uint256 timestamp;
        string challengeNonce;
    }

    Record[] private _records;

    event RecordAdded(
        string faceHash,
        string matchedUrl,
        uint256 timestamp
    );

    function addRecord(
        string calldata faceHash,
        string calldata matchedUrl,
        string calldata postHash,
        uint256 timestamp,
        string calldata challengeNonce
    ) external {
        _records.push(
            Record(
                faceHash,
                matchedUrl,
                postHash,
                timestamp,
                challengeNonce
            )
        );

        emit RecordAdded(
            faceHash,
            matchedUrl,
            timestamp
        );
    }

    function recordCount() external view returns (uint256) {
        return _records.length;
    }

    function getRecord(uint256 index)
        external
        view
        returns (
            string memory faceHash,
            string memory matchedUrl,
            string memory postHash,
            uint256 timestamp,
            string memory challengeNonce
        )
    {
        require(index < _records.length, "Index out of bounds");

        Record storage r = _records[index];

        return (
            r.faceHash,
            r.matchedUrl,
            r.postHash,
            r.timestamp,
            r.challengeNonce
        );
    }
}
